# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
This is the main file for the VSS Auto Calibration Microservice APIs using FastAPI.
"""

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.background import BackgroundTasks
from typing import Dict, Any, Union, List, Optional, NoReturn
import subprocess
import shutil
import tempfile
from random import randint
import os
from pathlib import Path
import sys
from datetime import datetime
from zipfile import ZipFile
from ruamel.yaml import YAML
import cv2
from utils.video_capture import open_video_capture
from utils.mp4_probe import (
    DeepStreamValidationUnavailableError,
    UnsupportedDeepStreamVideoError,
    read_mp4_resolution,
    require_deepstream_decode_support,
)
import signal
import asyncio
import re
import math
from utils.roi_verification import generate_bev_verification
import logging
import json
from fastapi.middleware.cors import CORSMiddleware

from utils import (
    AnnotationStorage,
    resolve_camera_annotation_block,
    export_calibration_for_project,
    export_image_calibration_for_project,
    StateManager,
    ViosClient,
    ViosClientError,
)
from utils.calibration_exporter import (
    GLOBAL_ROIS_TRIPWIRES_KEY,
    INCOMPLETE_TRIPWIRE_MESSAGE_PREFIX,
    strip_video_filename_extension,
)
from utils.global_annotation_projection import (
    compute_global_layout_camera_overlay,
    merge_global_projected_overlay,
)
from models import *
from rectification_service import (
    build_camera_states,
    preview_rectified_frame_png,
    materialize_rectified_alignment,
)

# Project State
#  INIT
#  READY
#  RUNNING
#  COMPLETED
#  ERROR

#-------- System Related Initialization ----------------------------------------#

# Custom float representer to prevent scientific notation in YAML output
def float_representer(dumper, value):
    if value != value:  # NaN
        text = '.nan'
    elif value == float('inf'):
        text = '.inf'
    elif value == float('-inf'):
        text = '-.inf'
    else:
        # Format with enough precision, strip trailing zeros
        text = f'{value:.10f}'.rstrip('0').rstrip('.')
    return dumper.represent_scalar('tag:yaml.org,2002:float', text)
yaml = YAML()
yaml.representer.add_representer(float, float_representer)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# API versioning
API_VERSION_PREFIX_ENV = os.getenv("API_VERSION_PREFIX")
API_VERSION_PREFIX = f"/{API_VERSION_PREFIX_ENV}" if API_VERSION_PREFIX_ENV is not None else ""

# TODO: Write liviness, readiness and startup probe, metrics and trace APIs
# TODO: Logging!

app = FastAPI(
    title="Auto Magic Calib API",
    description="Auto Magic Calib API",
    version="0.1.0",
    contact={
        "name": "NVIDIA",
        "url": "https://www.nvidia.com",
    },
    license_info={
        "name": "XX",
        "url": "https://www.nvidia.com"
    },
    openapi_url="/openapi.yaml",
    openapi_tags=[
        {
            "name": "Project Management",
            "description": "Project Management APIs for creating, listing, updating, and deleting projects"
        },
        {
            "name": "Data",
            "description": "Data Management APIs for uploading, listing and deleting project data"
        },
        {
            "name": "Calibration",
            "description": "Calibration APIs for running the Auto Magic Calib process and retrieving the results"
        },
        {
            "name": "Annotation",
            "description": "Annotation APIs for creating tripwire lines and tripwire directions"
        },
        {
            "name": "Health",
            "description": "Health check APIs for checking the health of the microservice"
        },
        {
            "name": "System",
            "description": "System-level APIs for capabilities and configuration"
        },
        {
            "name": "RTSP Capture",
            "description": (
                "RTSP stream capture APIs. Register RTSP cameras as VIOS sensors, "
                "record for a specified duration, then automatically add stored clips "
                "to a calibration project."
            )
        }
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins - restrict this in production
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Mount static files for production React build
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/assets", StaticFiles(directory=str(static_path / "assets")), name="assets")

# Serve React index.html at root
@app.get(f"{API_VERSION_PREFIX}/", response_class=HTMLResponse, include_in_schema=False)
async def serve_react_app():
    index_file = Path(__file__).parent / "static" / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse("<h1>API is running. Frontend build not found. Run 'npm run build' and copy dist/ to server/static/</h1>")

# State Manager Instance
StateManager = StateManager("projects/state.json")
DEFAULT_PROJECT_STATE_SAVE_INTERVAL_SECONDS = 5.0


def _get_project_state_save_interval() -> float:
    raw_interval = os.getenv("PROJECT_STATE_SAVE_INTERVAL_SECONDS", "5")
    try:
        interval = float(raw_interval)
    except ValueError:
        logger.warning(
            "Invalid PROJECT_STATE_SAVE_INTERVAL_SECONDS=%r; using %.1fs",
            raw_interval,
            DEFAULT_PROJECT_STATE_SAVE_INTERVAL_SECONDS,
        )
        return DEFAULT_PROJECT_STATE_SAVE_INTERVAL_SECONDS
    if not math.isfinite(interval) or interval <= 0:
        logger.warning(
            "PROJECT_STATE_SAVE_INTERVAL_SECONDS must be positive; using %.1fs",
            DEFAULT_PROJECT_STATE_SAVE_INTERVAL_SECONDS,
        )
        return DEFAULT_PROJECT_STATE_SAVE_INTERVAL_SECONDS
    return interval


PROJECT_STATE_SAVE_INTERVAL_SECONDS = _get_project_state_save_interval()

# In-memory cache
app.state.projects = {}
app.state.running_calibration = False
app.state.vggt_available = False
app.state.rtsp_supported = False

# RTSP capture sessions (persisted to state.json; key = session_id)
app.state.rtsp_sessions: dict = {}
# Running asyncio tasks for RTSP capture (not persisted; key = session_id)
app.state.rtsp_tasks: dict = {}
app.state.project_state_save_task: Optional[asyncio.Task] = None
app.state.project_state_save_lock = asyncio.Lock()

# VIOS server URL, required for the RTSP capture feature. Read once at import
# time; the startup hook validates it. Clients do not (cannot) override this
# per request — see RtspCaptureRequest.
VIOS_BASE_URL_ENV = os.getenv("VIOS_BASE_URL", "").strip().rstrip("/")

# Minimum RTSP capture duration. Shorter windows produce clips that are too
# brief for reliable calibration and stress the VIOS register → record →
# ingest pipeline (startup/verify alone consumes several seconds).
MIN_RTSP_DURATION_SECONDS = 60
MAX_RTSP_DURATION_SECONDS = 20 * 60


def _serialized_project_state_snapshot() -> str:
    """Serialize state on the event loop before handing immutable data to a worker."""
    state = {
        "projects": {
            project_id: {
                **dict(project),
                "calibration_log": None,
                "vggt_calibration_log": None,
                "postprocess_log": None,
                "rectification_log": None,
            }
            for project_id, project in app.state.projects.items()
        },
        "running_calibration": app.state.running_calibration,
        "rtsp_sessions": dict(app.state.rtsp_sessions),
    }
    return json.dumps(state, indent=4)


def _save_serialized_project_state(serialized_state: str) -> bool:
    """Atomically write already-serialized state outside the event loop."""
    return StateManager.save_serialized_state(serialized_state)


async def persist_project_state() -> bool:
    """Write a restart-safe snapshot without blocking request handling."""
    try:
        async with app.state.project_state_save_lock:
            serialized_state = _serialized_project_state_snapshot()
            write_task = asyncio.create_task(
                asyncio.to_thread(_save_serialized_project_state, serialized_state)
            )
            try:
                saved = await asyncio.shield(write_task)
            except asyncio.CancelledError:
                await write_task
                raise
            if not saved:
                logger.error("Failed to persist project state")
            return saved
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Failed to build or persist project state")
        return False


async def periodically_persist_project_state() -> None:
    """Persist in-memory state without relying on graceful service shutdown."""
    while True:
        await asyncio.sleep(PROJECT_STATE_SAVE_INTERVAL_SECONDS)
        try:
            await persist_project_state()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Periodic project state save failed")

# Startup: Load state from JSON file
@app.on_event("startup")
async def startup_event():
    '''
    Load the project state from the JSON file
    and set the project state to the in-memory cache
    if the JSON file is not found, set the project state to an empty dictionary
    and set the running calibration state to False
    '''
    try:
        state = StateManager.load_state()
        app.state.projects = state["projects"]
        app.state.running_calibration = state["running_calibration"]
        app.state.rtsp_sessions = state.get("rtsp_sessions", {})
        for project_id, project in app.state.projects.items():
            if "input_source_mode" not in project:
                current_files = set(project.get("video_files", []))
                rtsp_files = {
                    filename
                    for session in app.state.rtsp_sessions.values()
                    if session.get("project_id") == project_id
                    for filename in session.get("ingested_files", [])
                }
                project["input_source_mode"] = (
                    "rtsp" if current_files & rtsp_files else "manual" if current_files else None
                )
            project.setdefault("verified", False)
            project.setdefault("amc_export_ready", False)
            project.setdefault("vggt_export_ready", False)
            project.setdefault("postprocess_state", "INIT")
            project.setdefault("postprocess_pid", None)
            project.setdefault("postprocess_log", None)
            project.setdefault("rectification_state", "NOT_STARTED")
            project.setdefault("rectification_path", None)
            project.setdefault("rectification_pid", None)
            project.setdefault("rectification_log", None)
            if project["rectification_state"] == "RUNNING":
                project["rectification_state"] = "ERROR"
                project["rectification_pid"] = None
                project["rectification_log"] = None
            if project["postprocess_state"] == "RUNNING":
                project["postprocess_state"] = "ERROR"
                project["postprocess_pid"] = None
                project["postprocess_log"] = None
            # Processes and their background monitors do not survive a service
            # restart. Never leave the service globally blocked on stale state.
            for state_key, pid_key, log_key in (
                ("amc_state", "calibration_pid", "calibration_log"),
                ("vggt_state", "vggt_calibration_pid", "vggt_calibration_log"),
            ):
                if project.get(state_key) == "RUNNING":
                    project[state_key] = "ERROR"
                    project[pid_key] = None
                    project[log_key] = None
        app.state.running_calibration = False
        # Establish capability before restoring per-project VGGT readiness.
        # A persisted READY state is invalid if the model is no longer mounted.
        app.state.vggt_available = check_vggt_available()
        for project in app.state.projects.values():
            enable_vggt_if_eligible(project)
            refresh_project_state(project)
    except Exception as e:
        logger.error(f"\tFailed to load project state from JSON file: {e}")
        logger.error(f"\tSetting projects to an empty state")
        app.state.projects = {}
        app.state.running_calibration = False
        app.state.rtsp_sessions = {}
    # Sessions that were RECORDING or STARTING at shutdown can never be resumed;
    # mark them as ERROR so the user knows to re-initiate capture.
    for sid, sess in app.state.rtsp_sessions.items():
        if sess.get("status") in ("STARTING", "RECORDING", "STOPPING", "INGESTING"):
            sess["status"] = "ERROR"
            sess["error_message"] = "Service restarted while capture was in progress."
    logger.info(f"\tLoaded project state from state manager")
    logger.info(f"\n\tProjects: {json.dumps(app.state.projects, indent=4)}")
    logger.info(f"\n\tRunning calibration: {json.dumps(app.state.running_calibration, indent=4)}")

    if VIOS_BASE_URL_ENV:
        logger.info(f"\n\tRTSP capture enabled — VIOS_BASE_URL: {VIOS_BASE_URL_ENV}")
        app.state.rtsp_supported = True
    else:
        logger.info(
            "\n\tRTSP capture disabled — VIOS_BASE_URL not set. "
            "Set this env var to enable /rtsp/* endpoints."
        )
        app.state.rtsp_supported = False
    
    # Check VGGT availability
    app.state.vggt_available = check_vggt_available()
    logger.info(f"\n\tVGGT available: {app.state.vggt_available}")

    host_ip = os.getenv("HOST_IP", "localhost")
    ms_port = os.getenv("AUTO_MAGIC_CALIB_MS_PORT", "8000")
    logger.info(f"\n\tAuto Calibration MS accessible at: http://{host_ip}:{ms_port}{API_VERSION_PREFIX}")
    await persist_project_state()
    app.state.project_state_save_task = asyncio.create_task(periodically_persist_project_state())


# Shutdown: Save state to JSON file
@app.on_event("shutdown")
async def shutdown_event():
    '''
    Save the project state to the JSON file
    '''
    try:
        save_task = app.state.project_state_save_task
        if save_task is not None:
            save_task.cancel()
            try:
                await save_task
            except asyncio.CancelledError:
                pass
            app.state.project_state_save_task = None
        logger.info(f"\tSaving project state to state manager")
        await persist_project_state()
    except Exception as e:
        logger.error(f"\tSateManager failed to save state: {e}")

#------- API for health check --------------------------------------------------#

# Liveness check
@app.get(f"{API_VERSION_PREFIX}/live", tags=["Health"])
async def api_health() -> StandardResponse:
    return StandardResponse(
        code=0,
        message="VSS Auto Calibration Microservice is alive",
    )

# Readiness check
@app.get(f"{API_VERSION_PREFIX}/ready", tags=["Health"])
async def api_health() -> StandardResponse:
    return StandardResponse(
        code=0,
        message="VSS Auto Calibration Microservice is ready",
    )

# Startup check
@app.get(f"{API_VERSION_PREFIX}/startup", tags=["Health"])
async def api_health() -> StandardResponse:
    return StandardResponse(
        code=0,
        message="VSS Auto Calibration Microservice is up and ready to serve requests",
    )

#------- System API -------------------------------------------------#

# Get system capabilities
@app.get(f"{API_VERSION_PREFIX}/system/capabilities", tags=["System"])
async def get_system_capabilities() -> SystemCapabilitiesResponse:
    return SystemCapabilitiesResponse(
        code=0,
        vggt_available=app.state.vggt_available,
        rtsp_supported=app.state.rtsp_supported,
        running_calibration=bool(app.state.running_calibration),
        message="System capabilities retrieved successfully"
    )

# Get system information
@app.get(f"{API_VERSION_PREFIX}/metadata", tags=["System"])
async def get_system_information() -> MetadataResponse:
    return MetadataResponse(
        code=0,
        version="3.3.0",
        name="VSS Auto Calibration Microservice",
        description="VSS Auto Calibration Microservice",
        licenseInfo={
            "name": "NVIDIA",
        },
        message="Metadata retrieved successfully",
    )

#------- Project Management API --------------------------------------------------#

def _validate_project_name(project_name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,50}", project_name):
        raise HTTPException(
            status_code=422,
            detail="Project name must be 3-50 characters and contain only letters, numbers, hyphens, and underscores.",
        )


# Create the project directory and set the project state to INIT
@app.post(
    f"{API_VERSION_PREFIX}/create_project",
    tags=["Project Management"],
    summary="Create a new project with a unique ID",
    description="Create a new project with a unique ID and set the project state to INIT",
    response_model=None,
    responses={
        "200": {
            "description": "Project created successfully",
            "content": {
                "application/json": {
                    "schema": ProjectCreationResponse.model_json_schema()
                }
            }
        }
    },
)
async def create_project(project_name: str = Form(...)) -> Union[ProjectCreationResponse, HTTPException]:
    _validate_project_name(project_name)
    project_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{randint(1000, 9999)}"
    project_dir = Path(f"projects/project_{project_id}")
    try:
        Path(project_dir).mkdir(parents=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to Create Project {project_name}: {e}")

    project_info_detailed = ProjectInfoDetailed(
        project_id=project_id,
        project_name=project_name,
        project_dir=project_dir.as_posix(),
        project_state="INIT",
        amc_state="INIT",
        vggt_state="INIT",
        postprocess_state="INIT",
        rectification_state="NOT_STARTED",
        rectification_path=None,
        verified=False,
        video_directory=None,
        input_source_mode=None,
        video_files_count=0,
        video_files=[],
        groundtruth_file=False,
        alignment_file=False,
        layout_file=False,
        layout_filename=None,
        focal_length=[],
        amc_results_available=False,
        amc_export_ready=False,
        vggt_export_ready=False,
        calibration_pid=None,
        calibration_log=None,
        vggt_calibration_pid=None,
        vggt_calibration_log=None,
        postprocess_pid=None,
        postprocess_log=None,
        rectification_pid=None,
        rectification_log=None,
        camera_name_map={},
    )
    app.state.projects[project_id] = project_info_detailed.model_dump()
    project_info = ProjectInfoBase(**project_info_detailed.model_dump())
    return ProjectCreationResponse(
        **project_info.model_dump(),
        code=0,
        message=f"Project {project_name} Created Successfully"
    )


# List all the projects
@app.get(
    f"{API_VERSION_PREFIX}/list_projects", 
    tags=["Project Management"],
    summary="List all the projects",
    description="List all the projects",
    responses={
        "200": {
            "description": "Projects listed successfully",
            "model": ProjectListResponse,
            "content": {
                "application/json": {
                    "schema": ProjectListResponse.model_json_schema()
                }
            }
        }
    }
)
async def list_projects() -> ProjectListResponse:
    return ProjectListResponse(
        projects=list[ProjectInfo](app.state.projects.values()),
        code=0,
        message="Projects listed successfully"
    )


# Get the project info
@app.get(
    f"{API_VERSION_PREFIX}/get_project_info/{{project_id}}", 
    tags=["Project Management"],
    summary="Get the project info",
    description="Get the project info",
    responses={
        "200": {
            "description": "Project info retrieved successfully",
            "model": ProjectInfoResponse,
            "content": {
                "application/json": {
                    "schema": ProjectInfoResponse.model_json_schema()
                }
            }
        }
    }
)
async def get_project_info(project_id: str) -> ProjectInfoResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    return ProjectInfoResponse(
        project_info=ProjectInfo(**get_project_data_with_amc_check(project_id)),
        code=0,
        message="Project info retrieved successfully"
    )


# Update the project info
@app.patch(f"{API_VERSION_PREFIX}/update_project_info/{{project_id}}", tags=["Project Management"])
async def update_project_info(project_id: str, project_name: str = Form(...)) -> ProjectInfoResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    _validate_project_name(project_name)
    app.state.projects[project_id]["project_name"] = project_name
    return ProjectInfoResponse(
        project_info=ProjectInfo(**app.state.projects[project_id]),
        code=0,
        message="Project info updated successfully"
    )


# Delete the project
@app.delete(f"{API_VERSION_PREFIX}/delete_project/{{project_id}}", tags=["Project Management"])
async def delete_project(project_id: str) -> StandardResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Deletion is not allowed.")
    if app.state.projects[project_id].get("rectification_state") == "RUNNING":
        raise HTTPException(status_code=400, detail="Rectification is running. Deletion is not allowed.")

    # Block deletion while any RTSP capture session is in progress.
    active_states = ("STARTING", "RECORDING", "STOPPING", "INGESTING")
    active_sessions = [
        sid for sid, s in app.state.rtsp_sessions.items()
        if s.get("project_id") == project_id and s.get("status") in active_states
    ]
    if active_sessions:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Project has active RTSP session(s): {active_sessions}. "
                "Stop or wait for them to complete before deleting."
            ),
        )

    try:
        shutil.rmtree(app.state.projects[project_id]["project_dir"])
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to delete project {app.state.projects[project_id]['project_name']}: {e}")

    # Cascade: drop all RTSP session records for this project, and
    # best-effort deregister the VIOS sensors AMC MS created for each.
    for sid, s in [
        (sid, s) for sid, s in app.state.rtsp_sessions.items()
        if s.get("project_id") == project_id
    ]:
        await _deregister_session_sensors(s)
        app.state.rtsp_sessions.pop(sid, None)
        app.state.rtsp_tasks.pop(sid, None)

    del app.state.projects[project_id]
    return StandardResponse(
        code=0,
        message=f"Project {project_id} deleted successfully"
    )


@app.delete(f"{API_VERSION_PREFIX}/delete_projects", tags=["Project Management"])
async def delete_projects(request: DeleteProjectsRequest) -> DeleteProjectsResponse:
    """Delete selected projects in one request while preserving per-project guards."""
    # Preserve request order while avoiding duplicate deletion attempts.
    project_ids = list(dict.fromkeys(
        project_id.strip() for project_id in request.project_ids if project_id.strip()
    ))
    if not project_ids:
        raise HTTPException(status_code=400, detail="At least one project ID is required")

    deleted_project_ids = []
    skipped_projects = {}
    for project_id in project_ids:
        try:
            await delete_project(project_id)
            deleted_project_ids.append(project_id)
        except HTTPException as exc:
            skipped_projects[project_id] = str(exc.detail)
        except Exception as exc:
            logger.exception("Unexpected error deleting project %s", project_id)
            skipped_projects[project_id] = str(exc)

    skipped_count = len(skipped_projects)
    deleted_count = len(deleted_project_ids)
    if skipped_count == 0:
        code = 0
        message = f"{deleted_count} selected project(s) deleted successfully"
    else:
        code = 1
        message = (
            f"Deleted {deleted_count} selected project(s); "
            f"{skipped_count} project(s) could not be deleted"
        )

    return DeleteProjectsResponse(
        code=code,
        message=message,
        deleted_project_ids=deleted_project_ids,
        skipped_projects=skipped_projects,
        projects=list[ProjectInfo](app.state.projects.values()),
    )


# Reset the project
@app.delete(f"{API_VERSION_PREFIX}/reset_project/{{project_id}}", tags=["Project Management"])
async def reset_project(project_id: str) -> ProjectInfoResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Reset is not allowed.")
    if app.state.projects[project_id].get("rectification_state") == "RUNNING":
        raise HTTPException(status_code=400, detail="Rectification is running. Reset is not allowed.")
    app.state.projects[project_id]["project_state"] = "INIT"
    app.state.projects[project_id]["video_directory"] = None
    app.state.projects[project_id]["input_source_mode"] = None
    app.state.projects[project_id]["video_files_count"] = 0
    app.state.projects[project_id]["video_files"] = []
    app.state.projects[project_id]["groundtruth_file"] = False
    app.state.projects[project_id]["alignment_file"] = False
    app.state.projects[project_id]["layout_file"] = False
    app.state.projects[project_id]["layout_filename"] = None
    app.state.projects[project_id]["focal_length"] = []
    app.state.projects[project_id]["calibration_pid"] = None
    app.state.projects[project_id]["calibration_log"] = None
    app.state.projects[project_id]["vggt_calibration_pid"] = None
    app.state.projects[project_id]["vggt_calibration_log"] = None
    app.state.projects[project_id]["postprocess_pid"] = None
    app.state.projects[project_id]["postprocess_log"] = None
    app.state.projects[project_id]["amc_results_available"] = False
    app.state.projects[project_id]["amc_state"] = "INIT"
    app.state.projects[project_id]["vggt_state"] = "INIT"
    app.state.projects[project_id]["postprocess_state"] = "INIT"
    app.state.projects[project_id]["rectification_state"] = "NOT_STARTED"
    app.state.projects[project_id]["rectification_path"] = None
    app.state.projects[project_id]["verified"] = False
    app.state.projects[project_id]["rectification_pid"] = None
    app.state.projects[project_id]["rectification_log"] = None
    project_dir = Path(app.state.projects[project_id]["project_dir"])
    for file in project_dir.glob("*"):
        if file.is_dir():
            shutil.rmtree(file)
        else:
            file.unlink()
    
    return ProjectInfoResponse(
        project_info=ProjectInfo(**get_project_data_with_amc_check(project_id)),
        code=0,
        message="Project reset successfully"
    )


# Delete all the projects
@app.delete(f"{API_VERSION_PREFIX}/delete_all_projects", tags=["Project Management"])
async def delete_all_projects() -> DeleteAllProjectsResponse:
    active_states = ("STARTING", "RECORDING", "STOPPING", "INGESTING")
    projects_to_delete = []
    for project in app.state.projects.values():
        if project["project_state"] == "RUNNING":
            continue
        if project.get("rectification_state") == "RUNNING":
            continue
        # Skip projects whose RTSP capture is still in progress, matching the
        # single-project delete guard.
        pid = project["project_id"]
        if any(
            s.get("project_id") == pid and s.get("status") in active_states
            for s in app.state.rtsp_sessions.values()
        ):
            continue
        projects_to_delete.append(pid)
        try:
            shutil.rmtree(project["project_dir"])
        except Exception as e:
            raise HTTPException(status_code = 500, detail = f"Failed to delete project {project['project_name']}: {e}")
    for project_id in projects_to_delete:
        # Cascade: drop all RTSP session records for this project, and
        # best-effort deregister the VIOS sensors AMC MS created for each.
        for sid, s in [
            (sid, s) for sid, s in app.state.rtsp_sessions.items()
            if s.get("project_id") == project_id
        ]:
            await _deregister_session_sensors(s)
            app.state.rtsp_sessions.pop(sid, None)
            app.state.rtsp_tasks.pop(sid, None)
        del app.state.projects[project_id]
    if app.state.projects == {}:
        return DeleteAllProjectsResponse(
            code=0,
            projects=[],
            message="All projects deleted successfully"
        )
    else:
        return DeleteAllProjectsResponse(
            code=1,
            projects=list[ProjectInfo](app.state.projects.values()),
            message=(
                "Some projects could not be deleted (calibration running or "
                "RTSP capture in progress). See `projects` for the remaining list."
            )
        )


#------- Data Management API --------------------------------------------------#

_ACTIVE_RTSP_SESSION_STATES = ("STARTING", "RECORDING", "STOPPING", "INGESTING")
MANUAL_INPUT_SOURCE_MODE = "manual"
RTSP_INPUT_SOURCE_MODE = "rtsp"


def _has_active_rtsp_session(project_id: str) -> bool:
    """Return whether an RTSP capture can still ingest videos into project."""
    return any(
        session.get("project_id") == project_id
        and session.get("status") in _ACTIVE_RTSP_SESSION_STATES
        for session in app.state.rtsp_sessions.values()
    )


# Share video file directory instead of uploading video files
@app.post(f"{API_VERSION_PREFIX}/video_directory/{{project_id}}", tags=["Data"])
async def video_directory(project_id: str, directory: str = Form(...)) -> ProjectInfoResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Sharing video directory is not allowed.")
        # return {"message": "Calibration is running. Sharing video directory is not allowed."}
    if _has_active_rtsp_session(project_id):
        raise HTTPException(
            status_code=409,
            detail="RTSP capture is active. Wait for it to finish or stop it before sharing manual videos.",
        )
    if app.state.projects[project_id].get("input_source_mode") == "rtsp":
        raise HTTPException(
            status_code=409,
            detail="Project contains RTSP-ingested videos. Remove them before sharing manual videos.",
        )
    if not Path(directory).exists():
        raise HTTPException(status_code = 400, detail = "Video directory not found.")
        # return {"message": "Video directory not found."}
    if not Path(directory).is_dir():
        raise HTTPException(status_code = 400, detail = "Video directory is not a directory.")
        # return {"message": "Video directory is not a directory."}
    video_paths = sorted(
        [file for file in Path(directory).glob("*.mp4") if file.is_file()],
        key=lambda p: p.name,
    )
    if not video_paths:
        raise HTTPException(status_code=400, detail="No .mp4 files found in the video directory.")

    for video_path in video_paths:
        try:
            _require_1080p_video(video_path, display_name=video_path.name)
            await asyncio.to_thread(
                require_deepstream_decode_support, video_path, label=video_path.name
            )
        except (ValueError, DeepStreamValidationUnavailableError) as exc:
            logger.error(
                "Video directory share rejected for project %s (%s): %s",
                project_id,
                directory,
                exc,
            )
            status_code = (
                500
                if isinstance(exc, DeepStreamValidationUnavailableError)
                else 422 if isinstance(exc, UnsupportedDeepStreamVideoError) else 400
            )
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    _invalidate_linear_media(project_id)
    app.state.projects[project_id]["video_directory"] = directory
    app.state.projects[project_id]["video_files"] = [path.name for path in video_paths]
    app.state.projects[project_id]["video_files_count"] = len(video_paths)
    app.state.projects[project_id]["input_source_mode"] = MANUAL_INPUT_SOURCE_MODE
    app.state.projects[project_id]["project_state"] = "INIT"
    logger.info(
        "Shared video directory for project %s with %d file(s) at %dx%d: %s",
        project_id,
        len(video_paths),
        REQUIRED_VIDEO_WIDTH,
        REQUIRED_VIDEO_HEIGHT,
        [path.name for path in video_paths],
    )
    return ProjectInfoResponse(
        project_info=ProjectInfo(**app.state.projects[project_id]),
        code=0,
        message=(
            f"Video directory shared successfully. "
            f"All {len(video_paths)} file(s) are {REQUIRED_VIDEO_WIDTH}x{REQUIRED_VIDEO_HEIGHT}."
        ),
    )


# Delete video directory
@app.delete(f"{API_VERSION_PREFIX}/video_directory/{{project_id}}", tags=["Data"])
async def delete_video_directory(project_id: str) -> ProjectInfoResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Deleting video directory is not allowed.")
        # return {"message": "Calibration is running. Deleting video directory is not allowed."}
    if app.state.projects[project_id]["video_directory"] is None:
        raise HTTPException(status_code = 400, detail = "Video directory not found.")
        # return {"message": "Video directory not found."}
    _invalidate_linear_media(project_id)
    app.state.projects[project_id]["video_directory"] = None
    app.state.projects[project_id]["video_files"] = []
    app.state.projects[project_id]["video_files_count"] = 0
    app.state.projects[project_id]["input_source_mode"] = None
    app.state.projects[project_id]["project_state"] = "INIT"
    return ProjectInfoResponse(
        project_info=ProjectInfo(**app.state.projects[project_id]),
        code=0,
        message="Video directory deleted successfully"
    )


# All project input videos must be Full HD (pipeline and UI assume this frame size).
REQUIRED_VIDEO_WIDTH = 1920
REQUIRED_VIDEO_HEIGHT = 1080


def _require_1080p_video(video_path: Path, display_name: Optional[str] = None) -> tuple:
    """
    Ensure video is exactly 1920x1080. Raises ValueError with a user-facing message on failure.

    Resolution is read from MP4 container metadata rather than by opening a
    decoder, so files the FFmpeg-free OpenCV build cannot decode are still
    checked on their actual frame size.
    """
    label = display_name or video_path.name
    try:
        width, height = read_mp4_resolution(video_path, label=label)
    except ValueError as exc:
        logger.error(
            "Failed to read resolution for video '%s' at %s: %s",
            label,
            video_path,
            exc,
        )
        raise ValueError(
            f"Failed to read resolution for video '{label}'. "
            f"Ensure the file is a valid MP4. Details: {exc}"
        ) from exc

    if width != REQUIRED_VIDEO_WIDTH or height != REQUIRED_VIDEO_HEIGHT:
        logger.warning(
            "Rejected video '%s' (%s): resolution %dx%d does not match required %dx%d",
            label,
            video_path,
            width,
            height,
            REQUIRED_VIDEO_WIDTH,
            REQUIRED_VIDEO_HEIGHT,
        )
        raise ValueError(
            f"Video '{label}' has resolution {width}x{height}; "
            f"required resolution is {REQUIRED_VIDEO_WIDTH}x{REQUIRED_VIDEO_HEIGHT}."
        )

    logger.info("Video resolution check passed for '%s': %dx%d", label, width, height)
    return width, height


# Upload video files
@app.post(f"{API_VERSION_PREFIX}/upload_video_files/{{project_id}}", tags=["Data"])
async def upload_video_files(project_id: str, files: List[UploadFile] = File(...)) -> ProjectInfoResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Uploading video files is not allowed.")
        # return {"message": "Calibration is running. Uploading video files is not allowed."}
    if _has_active_rtsp_session(project_id):
        raise HTTPException(
            status_code=409,
            detail="RTSP capture is active. Wait for it to finish or stop it before uploading manual videos.",
        )
    if app.state.projects[project_id]["video_directory"] is not None:
        raise HTTPException(status_code = 400, detail = "Video directory is already shared. Uploading video files is not allowed.")
        # return {"message": "Video directory is already shared. Uploading video files is not allowed."}
    if app.state.projects[project_id].get("input_source_mode") == "rtsp":
        raise HTTPException(
            status_code=409,
            detail="Project contains RTSP-ingested videos. Remove them before uploading manual videos.",
        )
    _invalidate_linear_media(project_id)
    count = app.state.projects[project_id]["video_files_count"]
    accepted: List[str] = []
    for file in files:
        index = '{:02d}'.format(count)
        internal_name = f"cam_{index}.mp4"
        original_name = file.filename
        upload_path = Path(app.state.projects[project_id]["project_dir"]) / Path(internal_name)
        try:
            with open(upload_path, "wb") as buffer:
                buffer.write(file.file.read())
        except Exception as e:
            raise HTTPException(status_code = 500, detail = f"Failed to upload video file {file.filename} to {upload_path}: {e}")

        try:
            _require_1080p_video(upload_path, display_name=original_name or internal_name)
            await asyncio.to_thread(
                require_deepstream_decode_support,
                upload_path,
                label=original_name or internal_name,
            )
        except (ValueError, DeepStreamValidationUnavailableError) as exc:
            try:
                upload_path.unlink(missing_ok=True)
            except Exception as cleanup_err:
                logger.warning(
                    "Failed to remove rejected upload %s: %s",
                    upload_path,
                    cleanup_err,
                )
            logger.error(
                "Upload rejected for project %s: %s",
                project_id,
                exc,
            )
            status_code = (
                500
                if isinstance(exc, DeepStreamValidationUnavailableError)
                else 422 if isinstance(exc, UnsupportedDeepStreamVideoError) else 400
            )
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

        app.state.projects[project_id]["camera_name_map"][f"cam_{index}"] = original_name
        count += 1
        app.state.projects[project_id]["video_files"].append(internal_name)
        accepted.append(original_name or internal_name)

    app.state.projects[project_id]["video_files_count"] = count
    if accepted:
        app.state.projects[project_id]["input_source_mode"] = MANUAL_INPUT_SOURCE_MODE
    app.state.projects[project_id]["project_state"] = "INIT"
    logger.info(
        "Uploaded %d video(s) for project %s (all %dx%d): %s",
        len(accepted),
        project_id,
        REQUIRED_VIDEO_WIDTH,
        REQUIRED_VIDEO_HEIGHT,
        accepted,
    )
    return ProjectInfoResponse(
        project_info=ProjectInfo(**app.state.projects[project_id]),
        code=0,
        message=(
            f"Video files uploaded successfully. "
            f"All {len(accepted)} file(s) are {REQUIRED_VIDEO_WIDTH}x{REQUIRED_VIDEO_HEIGHT}."
        ),
    )


# List all the video files
@app.get(f"{API_VERSION_PREFIX}/list_video_files/{{project_id}}", tags=["Data"])
async def list_video_files(project_id: str):
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    return {
        "code": 0,
        "video_files": app.state.projects[project_id]["video_files"],
        "video_files_count": app.state.projects[project_id]["video_files_count"],
        "message": "Video files listed successfully"
    }


def _delete_exported_calibration_artifacts(project_dir: Path) -> None:
    """Remove calibration exports whose sensor indices no longer match videos."""
    project_name = project_dir.name
    stored_project_id = project_name.removeprefix("project_")
    export_names = [f"{stored_project_id}_image_mode_exported.json"]
    for suffix in ("", "_vggt"):
        for snapshot in ("exported", "projected"):
            export_names.append(f"{project_name}_{snapshot}{suffix}.json")

    for name in export_names:
        export_file = project_dir / name
        try:
            export_file.unlink(missing_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to delete calibration result {name}: {exc}",
            ) from exc


def _remap_rtsp_session_ingested_files(
    project_id: str, filename_map: Dict[str, str], deleted_filename: str
) -> None:
    """Keep completed RTSP session history consistent with reindexed videos."""
    for session in app.state.rtsp_sessions.values():
        if session.get("project_id") != project_id:
            continue
        ingested_files = session.get("ingested_files")
        if not isinstance(ingested_files, list):
            continue
        session["ingested_files"] = [
            filename_map.get(filename, filename)
            for filename in ingested_files
            if filename != deleted_filename
        ]


# Delete a video file, only allowed when video directory is not shared
@app.delete(f"{API_VERSION_PREFIX}/delete_video_file/{{project_id}}/{{video_name}}", tags=["Data"])
async def delete_video_file(project_id: str, video_name: str) -> ProjectInfoResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    project = app.state.projects[project_id]
    if project["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Deletion of video files is not allowed.")
        # return {"message": "Calibration is running. Deletion of video files is not allowed."}
    active_stage_names = {
        "amc_state": "AMC calibration",
        "vggt_state": "VGGT calibration",
        "postprocess_state": "Post-processing",
    }
    for state_key, stage_name in active_stage_names.items():
        if project.get(state_key) == "RUNNING":
            raise HTTPException(
                status_code=409,
                detail=f"{stage_name} is running. Deletion of video files is not allowed.",
            )
    if video_name not in project["video_files"]:
        raise HTTPException(status_code = 404, detail = "Video file not found")
    _invalidate_linear_media(project_id)

    project_dir = Path(project["project_dir"])
    
    # Uploaded videos are stored under server-generated, index-based names. Use
    # that invariant rather than the request value when constructing the path.
    deleted_index = project["video_files"].index(video_name)
    internal_name = f"cam_{deleted_index:02d}.mp4"

    # Delete the file
    try:
        (project_dir / internal_name).unlink()
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to delete video file {video_name}: {e}")
    
    # Get current state (work on copies to preserve original if operations fail)
    video_files = project["video_files"].copy()
    camera_name_map = project["camera_name_map"].copy()
    
    # Find index of deleted file and remove from map
    deleted_cam_id = f"cam_{deleted_index:02d}"
    if deleted_cam_id in camera_name_map:
        del camera_name_map[deleted_cam_id]
    
    # Remove from list
    video_files.remove(video_name)
    
    # Re-index remaining files
    new_video_files = []
    new_camera_name_map = {}
    camera_id_map = {}
    filename_map = {}
    for new_idx, old_filename in enumerate(video_files):
        old_idx = new_idx if new_idx < deleted_index else new_idx + 1
        old_cam_id = f"cam_{old_idx:02d}"
        old_internal_name = f"{old_cam_id}.mp4"
        new_cam_id = f"cam_{new_idx:02d}"
        new_filename = f"{new_cam_id}.mp4"
        camera_id_map[old_cam_id] = new_cam_id
        filename_map[old_internal_name] = new_filename
        
        # Rename file if index changed
        if old_internal_name != new_filename:
            old_path = project_dir / old_internal_name
            new_path = project_dir / new_filename
            if old_path.exists() and not new_path.exists():
                old_path.rename(new_path)
            elif new_path.exists():
                raise HTTPException(status_code=500, detail=f"Cannot rename {old_internal_name} to {new_filename}: destination already exists")
        
        new_video_files.append(new_filename)
        # Preserve original display name
        if old_cam_id in camera_name_map:
            new_camera_name_map[new_cam_id] = camera_name_map[old_cam_id]
    
    focal_length = project.get("focal_length", [])
    if focal_length:
        # A valid focal-length list remains valid after removing the matching
        # camera.  A pre-existing malformed list cannot be mapped reliably, so
        # invalidate it instead of carrying stale values into verification.
        if len(focal_length) == len(video_files) + 1:
            focal_length = focal_length[:deleted_index] + focal_length[deleted_index + 1:]
        else:
            focal_length = []

    storage = AnnotationStorage(project_dir)
    if not storage.remap_camera_annotations(camera_id_map, (deleted_cam_id,)):
        raise HTTPException(status_code=500, detail="Failed to remap camera annotations")

    # Alignment points and every calibration/export result are indexed by camera
    # position.  They cannot be safely transformed after deleting a middle video,
    # so clear them and require a fresh alignment/calibration run.  Source
    # annotations are remapped above and restored below.
    output_dir = project_dir / "output"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    manual_params = project_dir / "manual_rectification_params.json"
    if manual_params.is_file():
        manual_params.unlink()
    _delete_exported_calibration_artifacts(project_dir)
    _remap_rtsp_session_ingested_files(project_id, filename_map, internal_name)
    alignment_path = project_dir / "manual_adjustment" / "alignment_data.json"
    if alignment_path.exists():
        alignment_path.unlink()
    reconcile_alignment_artifacts(project_id)
    if not storage.sync_alignment_annotations():
        raise HTTPException(status_code=500, detail="Failed to restore remapped annotations")

    project["video_files"] = new_video_files
    project["camera_name_map"] = new_camera_name_map
    project["video_files_count"] = len(new_video_files)
    if not new_video_files:
        project["input_source_mode"] = None
    project["focal_length"] = focal_length
    project["project_state"] = "INIT"
    project["amc_state"] = "INIT"
    project["vggt_state"] = "INIT"
    project["postprocess_state"] = "INIT"
    project["amc_results_available"] = False
    
    return ProjectInfoResponse(
        project_info=ProjectInfo(**app.state.projects[project_id]),
        code=0,
        message="Video file deleted successfully"
    )


# Upload groundtruth files
@app.post(f"{API_VERSION_PREFIX}/upload_gt_file/{{project_id}}", tags=["Data"])
async def upload_gt_file(project_id: str, gt_file: UploadFile = File(...)) -> ProjectInfoResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Uploading groundtruth file is not allowed.")
        # return {"message": "Calibration is running. Uploading groundtruth file is not allowed."}
    if app.state.projects[project_id]["groundtruth_file"]:
        raise HTTPException(status_code = 400, detail = "Groundtruth file already uploaded. Please delete the existing groundtruth file first.")
        # return {"message": "Groundtruth file already uploaded. Please delete the existing groundtruth file first."}
    if not gt_file.filename or not gt_file.filename.endswith(".zip"):
        raise HTTPException(status_code = 400, detail = "Groundtruth file must be a zip file")
        # return {"message": "Groundtruth file must be a zip file"}
    upload_path = Path(app.state.projects[project_id]["project_dir"]) / Path("groundtruth")
    try:
        upload_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to create groundtruth directory: {e}")
        # return {"message": f"Failed to create groundtruth directory: {e}"}
    try:
        # Never use the client filename as a path. TemporaryFile also avoids
        # overwriting existing archives or following a client-named symlink.
        with tempfile.TemporaryFile(dir=upload_path) as buffer:
            buffer.write(gt_file.file.read())
            buffer.seek(0)
            with ZipFile(buffer, "r") as zip_ref:
                zip_ref.extractall(upload_path)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to upload or extract groundtruth data from {gt_file.filename}: {e}")
    app.state.projects[project_id]["groundtruth_file"] = True
    _invalidate_calibration_state(project_id)
    return ProjectInfoResponse(
        project_info=ProjectInfo(**app.state.projects[project_id]),
        code=0,
        message="Groundtruth file uploaded successfully"
    )


# Delete groundtruth file
@app.delete(f"{API_VERSION_PREFIX}/delete_gt_file/{{project_id}}", tags=["Data"])
async def delete_gt_file(project_id: str) -> ProjectInfoResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Deletion of groundtruth file is not allowed.")
        # return {"message": "Calibration is running. Deletion of groundtruth file is not allowed."}
    if not app.state.projects[project_id]["groundtruth_file"]:
        raise HTTPException(status_code = 400, detail = "Groundtruth file not found.")
        # return {"message": "Groundtruth file not found."}
    try:
        shutil.rmtree(Path(app.state.projects[project_id]["project_dir"]) / Path("groundtruth"))
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to delete groundtruth file: {e}")
        # return {"message": f"Failed to delete groundtruth file: {e}"}
    app.state.projects[project_id]["groundtruth_file"] = False
    _invalidate_calibration_state(project_id)
    return ProjectInfoResponse(
        project_info=ProjectInfo(**app.state.projects[project_id]),
        code=0,
        message="Groundtruth file deleted successfully"
    )


# Alignment JSON must contain at least four complete corresponding point sets.
# Single-camera sets are [camera, layout]; multi-camera sets are
# [camera_0, camera_1, layout].
MAX_ALIGNMENT_FILE_SIZE = 1024 * 1024
MIN_ALIGNMENT_POINT_SETS = 4


def _validate_alignment_json(file_content: bytes, is_single_cam: bool) -> list:
    """Parse and validate alignment JSON, raising ValueError with a user-facing message."""
    if not file_content:
        raise ValueError("Alignment file is empty.")
    if len(file_content) > MAX_ALIGNMENT_FILE_SIZE:
        raise ValueError("Alignment file must be 1 MB or smaller.")

    try:
        alignment_data = json.loads(file_content)
    except UnicodeDecodeError as exc:
        raise ValueError("Alignment file must contain valid UTF-8 JSON.") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid alignment JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}."
        ) from exc

    if not isinstance(alignment_data, list):
        raise ValueError("Alignment JSON must be an array of point sets.")
    if len(alignment_data) < MIN_ALIGNMENT_POINT_SETS:
        raise ValueError(
            f"Alignment JSON must contain at least {MIN_ALIGNMENT_POINT_SETS} point sets; "
            f"found {len(alignment_data)}."
        )

    expected_points = 2 if is_single_cam else 3
    format_description = (
        "[camera, layout]" if is_single_cam else "[camera_0, camera_1, layout]"
    )
    for set_index, point_set in enumerate(alignment_data, start=1):
        if not isinstance(point_set, list) or len(point_set) != expected_points:
            raise ValueError(
                f"Point set {set_index} must contain exactly {expected_points} points "
                f"in the format {format_description}."
            )

        for point_index, point in enumerate(point_set, start=1):
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError(
                    f"Point {point_index} in point set {set_index} must be [x, y]."
                )
            for coordinate_name, coordinate in zip(("x", "y"), point):
                if (
                    isinstance(coordinate, bool)
                    or not isinstance(coordinate, (int, float))
                    or (isinstance(coordinate, float) and not math.isfinite(coordinate))
                ):
                    raise ValueError(
                        f"Coordinate {coordinate_name} of point {point_index} in point set "
                        f"{set_index} must be a finite number."
                    )
                if coordinate < 0:
                    raise ValueError(
                        f"Coordinate {coordinate_name} of point {point_index} in point set "
                        f"{set_index} cannot be negative."
                    )

    return alignment_data


# Upload alignment file
@app.post(f"{API_VERSION_PREFIX}/upload_alignment/{{project_id}}", tags=["Data"])
async def upload_alignment(
    project_id: str,
    alignment_file: UploadFile = File(...),
    coord_space: str = Query(
        "original",
        description=(
            "original: pre-rectification / CLI alignment_data_orig coordinates (default). "
            "rectified: points match lens-corrected frames (UI draw/save)."
        ),
    ),
) -> AlignmentFileResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Uploading alignment file is not allowed.")
        # return {"message": "Calibration is running. Uploading alignment file is not allowed."}
    space = (coord_space or "original").strip().lower()
    if space not in {"rectified", "original"}:
        raise HTTPException(
            status_code=400,
            detail="coord_space must be 'rectified' or 'original'",
        )
    # Existing alignment data is replaced so the UI can save edits without a delete step.
    replacing_existing = bool(app.state.projects[project_id]["alignment_file"])
    filename = alignment_file.filename or ""
    if not filename.lower().endswith(".json"):
        raise HTTPException(status_code = 400, detail = "Alignment file must be a json file")
        # return {"message": "Alignment file must be a json file"}

    project = app.state.projects[project_id]
    is_single_cam = project["video_files_count"] == 1
    upload_path = Path(project["project_dir"]) / Path("manual_adjustment")
    output_path = Path(project["project_dir"]) / Path("output") / Path("alignment_data")
    manual_alignment_path = upload_path / "alignment_data.json"
    rectified_alignment_path = output_path / "alignment_data.json"
    orig_alignment_path = output_path / "alignment_data_orig.json"
    partial_alignment_path = output_path / "alignment_data_partial.json"

    # Create directories if they don't exist
    try:
        upload_path.mkdir(parents=True, exist_ok=True)
        output_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to create directories: {e}")
    
    # Read the file content once
    try:
        file_content = alignment_file.file.read()
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to read alignment file {alignment_file.filename}: {e}")

    # Reject malformed data before touching existing alignment files on disk.
    try:
        alignment_data = _validate_alignment_json(
            file_content,
            is_single_cam=is_single_cam,
        )
    except ValueError as exc:
        logger.warning(
            "Rejected alignment upload '%s' for project %s: %s",
            filename,
            project_id,
            exc,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    persisted_paths: List[Path] = []
    try:
        if space == "rectified":
            # UI draw/save path: points already match rectified frames.
            with open(manual_alignment_path, "wb") as buffer:
                buffer.write(file_content)
            with open(rectified_alignment_path, "wb") as buffer:
                buffer.write(file_content)
            persisted_paths = [manual_alignment_path, rectified_alignment_path]
            # Drop stale orig/partial so SV post-process cannot mix spaces.
            if orig_alignment_path.exists():
                orig_alignment_path.unlink()
            if partial_alignment_path.exists():
                partial_alignment_path.unlink()
            message = (
                "Alignment file updated successfully"
                if replacing_existing
                else "Alignment file uploaded successfully"
            )
        else:
            # CLI / file-upload path: store as orig and materialize when linear media exists.
            with open(orig_alignment_path, "wb") as buffer:
                buffer.write(file_content)
            if rectified_alignment_path.exists():
                rectified_alignment_path.unlink()
            if partial_alignment_path.exists():
                partial_alignment_path.unlink()

            if project.get("rectification_state") == "COMPLETED":
                try:
                    materialized = materialize_rectified_alignment(
                        Path(project["project_dir"]) / "output",
                        is_single_cam=is_single_cam,
                    )
                except Exception as exc:
                    logger.exception(
                        "Failed to materialize rectified alignment for project %s",
                        project_id,
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            "Saved original alignment, but failed to convert it into "
                            f"rectified coordinates: {exc}"
                        ),
                    ) from exc
                shutil.copy2(materialized, manual_alignment_path)
                persisted_paths = [
                    orig_alignment_path,
                    rectified_alignment_path,
                    manual_alignment_path,
                ]
                message = (
                    "Original alignment uploaded and converted to rectified coordinates"
                )
            else:
                # Keep a copy for get_alignment until rectification produces the rectified file.
                with open(manual_alignment_path, "wb") as buffer:
                    buffer.write(file_content)
                persisted_paths = [orig_alignment_path, manual_alignment_path]
                message = (
                    "Original alignment uploaded. It will be converted to rectified "
                    "coordinates when linear media preparation completes."
                )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to upload alignment file {alignment_file.filename}: {e}")
        # return {"message": f"Failed to upload alignment file {alignment_file.filename}: {e}"}
    _require_persisted_artifacts(
        persisted_paths,
        action=f"Alignment upload of {alignment_file.filename}",
    )
    app.state.projects[project_id]["alignment_file"] = True
    _invalidate_calibration_state(project_id)
    if replacing_existing and space == "rectified":
        logger.info(
            "Replaced existing alignment file for project %s with %d point sets (%s)",
            project_id,
            len(alignment_data),
            space,
        )
    else:
        logger.info(
            "Uploaded alignment file for project %s with %d point sets (%s)",
            project_id,
            len(alignment_data),
            space,
        )
    return AlignmentFileResponse(
        code=0,
        alignment_file=app.state.projects[project_id]["alignment_file"],
        message=message
    )


# Get the alignment file
@app.get(f"{API_VERSION_PREFIX}/get_alignment/{{project_id}}", tags=["Data"])
async def get_alignment(project_id: str) -> FileResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if not app.state.projects[project_id]["alignment_file"]:
        raise HTTPException(status_code = 400, detail = "Alignment file not found.")
        # return {"message": "Alignment file not found."}
    project_dir = Path(app.state.projects[project_id]["project_dir"])
    # Prefer rectified output (matches Manual Alignment / SV post-process frames).
    candidates = [
        project_dir / "output" / "alignment_data" / "alignment_data.json",
        project_dir / "manual_adjustment" / "alignment_data.json",
    ]
    for alignment_path in candidates:
        if alignment_path.is_file():
            return FileResponse(path=alignment_path, media_type="application/json")
    app.state.projects[project_id]["alignment_file"] = False
    app.state.projects[project_id]["project_state"] = "INIT"
    raise HTTPException(status_code = 400, detail = "Alignment file not found.")
        # return {"message": "Alignment file not found."}


# Delete alignment file
@app.delete(f"{API_VERSION_PREFIX}/delete_alignment/{{project_id}}", tags=["Data"])
async def delete_alignment(project_id: str) -> AlignmentFileResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Deletion of alignment file is not allowed.")
        # return {"message": "Calibration is running. Deletion of alignment file is not allowed."}
    if not app.state.projects[project_id]["alignment_file"]:
        raise HTTPException(status_code = 400, detail = "Alignment file not found.")
        # return {"message": "Alignment file not found."}
    
    project_dir = Path(app.state.projects[project_id]["project_dir"])
    alignment_paths = [
        project_dir / "manual_adjustment" / "alignment_data.json",
        project_dir / "output" / "alignment_data" / "alignment_data.json",
        project_dir / "output" / "alignment_data" / "alignment_data_orig.json",
        project_dir / "output" / "alignment_data" / "alignment_data_partial.json",
    ]
    try:
        for alignment_path in alignment_paths:
            if alignment_path.exists():
                alignment_path.unlink()
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to delete alignment file: {e}")
        # return {"message": f"Failed to delete alignment file: {e}"}
    app.state.projects[project_id]["alignment_file"] = False
    _invalidate_calibration_state(project_id)
    return AlignmentFileResponse(
        code=0,
        alignment_file=app.state.projects[project_id]["alignment_file"],
        message="Alignment file deleted successfully"
    )


# Upload layout file
@app.post(f"{API_VERSION_PREFIX}/upload_layout/{{project_id}}", tags=["Data"])
async def upload_layout(project_id: str, layout_file: UploadFile = File(...)) -> LayoutFileResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Uploading layout file is not allowed.")
        # return {"message": "Calibration is running. Uploading layout file is not allowed."} 
    if app.state.projects[project_id]["layout_file"]:
        raise HTTPException(status_code = 400, detail = "Layout file already uploaded. Please delete the existing file first.")
        # return {"message": "Layout file already uploaded. Please delete the existing layout file first."}
    if not layout_file.filename.endswith(".png"):
        raise HTTPException(status_code = 400, detail = "Layout file must be a png file")
        # return {"message": "Layout file must be a json file"}
    project_dir = Path(app.state.projects[project_id]["project_dir"])
    manual_adjustment_path = project_dir / "manual_adjustment"
    alignment_data_path = project_dir / "output" / "alignment_data"
    
    # Create both directories if they don't exist
    try:
        manual_adjustment_path.mkdir(parents=True, exist_ok=True)
        alignment_data_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to create directories: {e}")
    
    # Read file content once
    file_content = layout_file.file.read()
    if not file_content:
        raise HTTPException(status_code = 400, detail = f"Layout file {layout_file.filename} is empty")
    
    # Save to both locations
    try:
        with open(manual_adjustment_path / "layout.png", "wb") as buffer:
            buffer.write(file_content)
        with open(alignment_data_path / "layout.png", "wb") as buffer:
            buffer.write(file_content)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to upload layout file {layout_file.filename}: {e}")
        # return {"message": f"Failed to upload layout file {layout_file.filename}: {e}"}
    _require_persisted_artifacts(
        (manual_adjustment_path / "layout.png", alignment_data_path / "layout.png"),
        action=f"Layout upload of {layout_file.filename}",
    )
    app.state.projects[project_id]["layout_file"] = True
    app.state.projects[project_id]["layout_filename"] = layout_file.filename
    _invalidate_calibration_state(project_id)
    return LayoutFileResponse(
        code=0,
        layout_file=app.state.projects[project_id]["layout_file"],
        message="Layout file uploaded successfully"
    )


# Get the layout file
@app.get(f"{API_VERSION_PREFIX}/get_layout/{{project_id}}", tags=["Data"])
async def get_layout(project_id: str) -> FileResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if not app.state.projects[project_id]["layout_file"]:
        raise HTTPException(status_code = 400, detail = "Layout file not found.")
        # return {"message": "Layout file not found."}
    layout_path = Path(app.state.projects[project_id]["project_dir"]) / Path("manual_adjustment") / Path("layout.png")
    if layout_path.exists():
        return FileResponse(path = layout_path, media_type = "image/png")
    else:
        app.state.projects[project_id]["layout_file"] = False
        app.state.projects[project_id]["project_state"] = "INIT"
        raise HTTPException(status_code = 400, detail = "Layout file not found.")
        # return {"message": "Layout file not found."}


# Delete layout file
@app.delete(f"{API_VERSION_PREFIX}/delete_layout/{{project_id}}", tags=["Data"])
async def delete_layout(project_id: str) -> LayoutFileResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if not app.state.projects[project_id]["layout_file"]:
        raise HTTPException(status_code = 400, detail = "Layout file not found.")
        # return {"message": "Layout file not found."}
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Deletion of layout file is not allowed.")
        # return {"message": "Calibration is running. Deletion of layout file is not allowed."}
    
    project_dir = Path(app.state.projects[project_id]["project_dir"])
    
    # Delete from both locations
    try:
        manual_layout = project_dir / "manual_adjustment" / "layout.png"
        alignment_layout = project_dir / "output" / "alignment_data" / "layout.png"
        
        if manual_layout.exists():
            manual_layout.unlink()
        if alignment_layout.exists():
            alignment_layout.unlink()
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to delete layout file: {e}")
        # return {"message": f"Failed to delete layout file: {e}"}
    app.state.projects[project_id]["layout_file"] = False
    app.state.projects[project_id]["layout_filename"] = None
    _invalidate_calibration_state(project_id)
    return LayoutFileResponse(
        code=0,
        layout_file=app.state.projects[project_id]["layout_file"],
        message="Layout file deleted successfully"
    )


# Uplaod the focal length
@app.post(f"{API_VERSION_PREFIX}/upload_focal_length/{{project_id}}", tags=["Data"])
async def upload_focal_length(project_id: str, focal_length: List[float] = Form(...)) -> FocalLengthResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if app.state.projects[project_id]["video_files_count"] == 0:
        raise HTTPException(status_code = 400, detail = "Please upload the video files first")
        # return {"message": "Please upload the video files first"}
    if (len(focal_length) > 0) and (len(focal_length) != app.state.projects[project_id]["video_files_count"]):
        raise HTTPException(status_code = 400, detail = "Focal length count should be same as number of video files uploaded")
        # return {"message": "Focal length count should be same as number of video files uploaded"}
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Uploading focal length is not allowed.")
        # return {"message": "Calibration is running. Uploading focal length is not allowed."}
    app.state.projects[project_id]["focal_length"] = focal_length
    _invalidate_calibration_state(project_id)
    return FocalLengthResponse(
        code=0,
        focal_length=app.state.projects[project_id]["focal_length"],
        message="Focal length set successfully"
    )


# Get the focal length
@app.get(f"{API_VERSION_PREFIX}/get_focal_length/{{project_id}}", tags=["Data"])
async def get_focal_length(project_id: str) -> FocalLengthResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    return FocalLengthResponse(
        code= 0,
        focal_length=app.state.projects[project_id]["focal_length"],
        message="Focal length retrieved successfully"
    )


# Delete the focal length
@app.delete(f"{API_VERSION_PREFIX}/delete_focal_length/{{project_id}}", tags = ["Data"])
async def delete_focal_length(project_id: str) -> FocalLengthResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, details = "Project not found")
        # return {"message": "Project not found"}
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Deletion of focal length is not allowed.")
        # return {"message": "Calibration is running. Deletion of focal length is not allowed."}
    app.state.projects[project_id]["focal_length"] = []
    _invalidate_calibration_state(project_id)
    return FocalLengthResponse(
        code=0,
        focal_length=app.state.projects[project_id]["focal_length"],
        message="Focal length deleted successfully"
    )

'''
# Update config files
def update_params(config_file_path, key, value):
    # TODO: Handle comments in the config file
    search_string = r'{key}:.*'.format(key=key)
    # Add quotes around string values for proper YAML formatting
    if isinstance(value, str):
        formatted_value = f'"{value}"'
    elif isinstance(value, bool):
        formatted_value = "True" if value else "False"
    elif isinstance(value, float):
        rounded_value = round(value, 10)
        formatted_value = f'{rounded_value:.10f}'.rstrip('0').rstrip('.')
    else:
        formatted_value = str(value)
    replace_string = r'{key}: {value}'.format(key=key, value=formatted_value)
    
    with open(config_file_path, "r") as file:
        lines = file.readlines()
    for index, line in enumerate(lines):
        pattern = re.search(search_string, line)
        print(pattern)
        if pattern:
            result = re.sub(search_string, replace_string, line)
            lines[index] = result
    with open(config_file_path, "w") as file:
        file.writelines(lines)
'''


# Check if AMC result outputs exist for this project mode
def check_amc_results_exist(project_dir: str, video_files_count: int = 0) -> bool:
    """Return True when completed AMC artifacts for this camera mode are on disk.

    Single-camera AMC writes only under ``single_view_results``. Multi-camera AMC
    also produces ``multi_view_results``. Do not require MV outputs for single-cam,
    or ``amc_results_available`` stays false after a successful single-cam run.
    """
    sv_results = Path(project_dir) / "output" / "single_view_results"
    mv_results = Path(project_dir) / "output" / "multi_view_results"
    if video_files_count <= 1:
        # Single-camera AMC writes only single_view_results.
        return sv_results.is_dir()
    # Multi-camera AMC also produces multi_view_results.
    return sv_results.is_dir() and mv_results.is_dir()


def _scaled_world_dir(project_dir: str, result_type: str) -> Path:
    """Layout post-process output holding world-scaled camera matrices."""
    root = Path(project_dir) / "output"
    if result_type == "vggt":
        root = root / "vggt_results"
    return root / "multi_view_results" / "BA_output" / "results_ba_scaled_world"


def check_export_ready(
    project_dir: str, video_files_count: int = 0, result_type: str = "amc"
) -> bool:
    """True when post-processed world-scaled camera params exist for export.

    Multi-cam: layout post-process writes one ``camInfo_XX.yaml`` per camera under
    ``results_ba_scaled_world``. Partial output must not count as ready.
    Single-cam AMC: SV post-process writes ``camInfo_world.yaml`` inside the AMC
    job (no separate layout post-process / MV3DT). VGGT is multi-cam only.
    """
    if video_files_count <= 1:
        if result_type == "vggt":
            return False
        return _is_non_empty_file(
            Path(project_dir)
            / "output"
            / "single_view_results"
            / "cam_00"
            / "camInfo_world.yaml"
        )
    scaled_dir = _scaled_world_dir(project_dir, result_type)
    return all(
        _is_non_empty_file(scaled_dir / f"camInfo_{index:02d}.yaml")
        for index in range(video_files_count)
    )


def refresh_project_state(project: dict) -> None:
    """Derive aggregate UI state; per-pipeline state remains authoritative."""
    if any(
        project.get(key) == "RUNNING"
        for key in ("rectification_state", "amc_state", "vggt_state", "postprocess_state")
    ):
        project["project_state"] = "RUNNING"
    elif (
        any(project.get(key) == "COMPLETED" for key in ("amc_state", "vggt_state"))
        and (
            project.get("video_files_count", 1) <= 1
            or project.get("postprocess_state") == "COMPLETED"
        )
    ):
        # Pipeline states remain visible independently. Keep a valid AMC or
        # VGGT result accessible if the other independent pipeline failed. For
        # multi-camera projects, final completion also requires post-processing.
        project["project_state"] = "COMPLETED"
    elif any(
        project.get(key) == "ERROR"
        for key in ("rectification_state", "amc_state", "vggt_state", "postprocess_state")
    ):
        project["project_state"] = "ERROR"
    else:
        # Verification validates source inputs only. A project is ready to
        # calibrate only after rectified media has also been staged.
        project["project_state"] = (
            "READY"
            if project.get("verified") and project.get("rectification_state") == "COMPLETED"
            else "INIT"
        )
    # Jobs are serialized because they mutate shared configuration.  Keep the
    # service-wide lock derived from authoritative per-project job state so a
    # completion or stop in one project cannot unlock another active job.
    app.state.running_calibration = any(
        any(candidate.get(key) == "RUNNING" for key in (
            "rectification_state", "amc_state", "vggt_state", "postprocess_state"
        ))
        for candidate in app.state.projects.values()
    )


def _require_completed_pipeline(project_id: str, pipeline: str) -> dict:
    """Allow read access to completed AMC/VGGT results even if the other job is RUNNING.

    Aggregate ``project_state`` becomes RUNNING whenever any pipeline runs, so result
    endpoints must gate on the specific pipeline state (``amc_state`` / ``vggt_state``)
    rather than the aggregate flag.
    """
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    project = app.state.projects[project_id]
    if pipeline not in ("amc", "vggt"):
        raise HTTPException(status_code=400, detail="pipeline must be 'amc' or 'vggt'")

    state_key = "amc_state" if pipeline == "amc" else "vggt_state"
    label = "AMC" if pipeline == "amc" else "VGGT"
    state = project.get(state_key)

    if state == "COMPLETED":
        return project
    if state == "RUNNING":
        raise HTTPException(
            status_code=400,
            detail=f"{label} calibration is still running. Please wait for it to complete.",
        )
    if state == "ERROR":
        raise HTTPException(
            status_code=400,
            detail=f"{label} calibration is in an error state. Results are not available.",
        )
    raise HTTPException(
        status_code=400,
        detail=f"{label} calibration must be completed first.",
    )


def _require_any_completed_calibration(project_id: str) -> dict:
    """Allow access when at least one calibration pipeline has completed results."""
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    project = app.state.projects[project_id]
    if project.get("amc_state") == "COMPLETED" or project.get("vggt_state") == "COMPLETED":
        return project
    if project.get("amc_state") == "RUNNING" or project.get("vggt_state") == "RUNNING":
        raise HTTPException(
            status_code=400,
            detail="Calibration is still running. Please wait for it to complete.",
        )
    if project.get("amc_state") == "ERROR" or project.get("vggt_state") == "ERROR":
        raise HTTPException(
            status_code=400,
            detail="Calibration is in an error state. Results are not available.",
        )
    raise HTTPException(
        status_code=400,
        detail="Please run the calibration first.",
    )


def enable_amc_if_eligible(project: dict) -> None:
    if (
        project.get("verified")
        and project.get("rectification_state") == "COMPLETED"
        and project.get("amc_state") == "INIT"
    ):
        project["amc_state"] = "READY"


def _is_non_empty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _layout_artifact_paths(project_dir: Path) -> tuple:
    """Durable upload copy and the runtime copy consumed by post-processing."""
    return (
        project_dir / "manual_adjustment" / "layout.png",
        project_dir / "output" / "alignment_data" / "layout.png",
    )


def _alignment_artifact_paths(project_dir: Path) -> tuple:
    """Durable copy, runtime rectified copy, and the original-space seed."""
    return (
        project_dir / "manual_adjustment" / "alignment_data.json",
        project_dir / "output" / "alignment_data" / "alignment_data.json",
        project_dir / "output" / "alignment_data" / "alignment_data_orig.json",
    )


def missing_postprocess_artifacts(project: dict) -> List[str]:
    """Return runtime layout/alignment files post-processing cannot run without."""
    project_dir = Path(project["project_dir"])
    _, runtime_layout = _layout_artifact_paths(project_dir)
    _, runtime_alignment, _ = _alignment_artifact_paths(project_dir)
    return [
        path.as_posix()
        for path in (runtime_layout, runtime_alignment)
        if not _is_non_empty_file(path)
    ]


def _require_persisted_artifacts(paths, action: str) -> None:
    """Fail an upload/save that did not land every file downstream stages read."""
    missing = [path.as_posix() for path in paths if not _is_non_empty_file(path)]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"{action} did not persist required files: {', '.join(missing)}",
        )


def reconcile_alignment_artifacts(project_id: str) -> None:
    """Make layout/alignment flags agree with the files on disk.

    Every rectification or source-media change wipes output/, so the runtime
    copies under output/alignment_data/ are rebuilt here from the durable
    manual_adjustment/ upload (or re-derived from the original-space seed).
    Flags are recomputed afterwards so the UI cannot advertise inputs that
    post-processing would then reject.
    """
    project = app.state.projects[project_id]
    project_dir = Path(project["project_dir"])
    manual_layout, runtime_layout = _layout_artifact_paths(project_dir)
    manual_alignment, runtime_alignment, orig_alignment = _alignment_artifact_paths(
        project_dir
    )

    if _is_non_empty_file(manual_layout) and not _is_non_empty_file(runtime_layout):
        try:
            runtime_layout.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(manual_layout, runtime_layout)
        except Exception:
            logger.exception(
                "Failed to restore runtime layout for project %s", project_id
            )

    project["layout_file"] = _is_non_empty_file(manual_layout) or _is_non_empty_file(
        runtime_layout
    )
    if not project["layout_file"]:
        project["layout_filename"] = None

    # Rectified points are only meaningful against staged linear media, so
    # never rebuild them while rectification is incomplete.
    if (
        not _is_non_empty_file(runtime_alignment)
        and project.get("rectification_state") == "COMPLETED"
    ):
        if _is_non_empty_file(orig_alignment):
            _sync_alignment_after_linear_media(project_id)
        elif _is_non_empty_file(manual_alignment):
            # A rectified-space save leaves no seed to re-derive from; the
            # durable copy is the only way back after output/ was wiped.
            try:
                runtime_alignment.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(manual_alignment, runtime_alignment)
            except Exception:
                logger.exception(
                    "Failed to restore runtime alignment for project %s", project_id
                )

    project["alignment_file"] = any(
        _is_non_empty_file(path)
        for path in (manual_alignment, runtime_alignment, orig_alignment)
    )


def check_postprocess_ready(project: dict) -> bool:
    """Return whether shared multi-camera post-processing has valid inputs."""
    if project.get("video_files_count", 0) <= 1:
        return False

    project_dir = Path(project["project_dir"])
    output_dir = project_dir / "output"
    alignment_ready = not missing_postprocess_artifacts(project)
    amc_ready = (
        output_dir / "multi_view_results" / "BA_output" / "results_ba" / "refined"
    ).is_dir()
    vggt_ready = (
        output_dir
        / "vggt_results"
        / "multi_view_results"
        / "BA_output"
        / "results_ba"
        / "refined"
    ).is_dir()
    return alignment_ready and (amc_ready or vggt_ready)


def refresh_postprocess_state(project: dict, force_ready: bool = False) -> None:
    """Refresh readiness without overriding an active or completed run."""
    state = project.get("postprocess_state", "INIT")
    if state == "RUNNING":
        return
    # Raw calibration re-runs overwrite scaled/derived trees; wait until they finish.
    if project.get("amc_state") == "RUNNING" or project.get("vggt_state") == "RUNNING":
        project["postprocess_state"] = "INIT"
        return
    if check_postprocess_ready(project) and (force_ready or state != "COMPLETED"):
        project["postprocess_state"] = "READY"
    elif state != "COMPLETED":
        project["postprocess_state"] = "INIT"


def get_project_data_with_amc_check(project_id: str) -> dict:
    """Get project data with real-time amc_results_available flag"""
    reconcile_alignment_artifacts(project_id)
    refresh_postprocess_state(app.state.projects[project_id])
    enable_amc_if_eligible(app.state.projects[project_id])
    enable_vggt_if_eligible(app.state.projects[project_id])
    refresh_project_state(app.state.projects[project_id])
    project_data = app.state.projects[project_id].copy()
    video_count = project_data.get("video_files_count", 0)
    project_dir = project_data["project_dir"]
    project_data["amc_results_available"] = (
        project_data.get("amc_state") == "COMPLETED"
        and check_amc_results_exist(project_dir, video_count)
    )
    # Per-pipeline export readiness: independent of shared postprocess_state so a
    # later VGGT run that resets postprocess to READY does not hide AMC exports
    # whose world-scaled artifacts are still on disk.
    project_data["amc_export_ready"] = (
        project_data.get("amc_state") == "COMPLETED"
        and check_export_ready(project_dir, video_count, "amc")
    )
    project_data["vggt_export_ready"] = (
        project_data.get("vggt_state") == "COMPLETED"
        and check_export_ready(project_dir, video_count, "vggt")
    )
    for key in ("amc_results_available", "amc_export_ready", "vggt_export_ready"):
        app.state.projects[project_id][key] = project_data[key]
    return project_data


def check_vggt_available() -> bool:
    """Check if VGGT model and dependencies are available"""
    vggt_model_path = Path("../models/vggt/model.pt")
    vggt_core_path = Path("../core/VGGT/")
    return vggt_model_path.exists() and vggt_core_path.exists()


def _raise_export_failure(message: str, fallback: str) -> None:
    """Incomplete tripwires are a user-fixable 400; everything else stays a 500."""
    text = message or fallback
    if text.startswith(INCOMPLETE_TRIPWIRE_MESSAGE_PREFIX):
        raise HTTPException(status_code=400, detail=text)
    raise HTTPException(status_code=500, detail=text)


@app.post(f"{API_VERSION_PREFIX}/config/{{project_id}}", tags=["Data"])
async def update_config_params(project_id: str, config_params: ConfigParams):
    print("Debug")
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if (app.state.projects[project_id]["project_state"] == "RUNNING") or (app.state.running_calibration == True):
        raise HTTPException(status_code = 400, detail = "Calibration is running. Updating config parameters is not allowed.")
        # return {"message": "Calibration is running. Updating config parameters is not allowed."}
    if app.state.projects[project_id].get("rectification_state") == "RUNNING":
        raise HTTPException(status_code=400, detail="Rectification is running. Updating configuration is not allowed.")
    config_path = Path("../configs/config_AutoMagicCalib")
    camera_matrix_path = config_path / Path("camera_matrix_config.yaml")
    sv_amc_path = config_path / Path("sv_amc_config.yaml")
    eval_path = config_path / Path("eval_config.yaml")
    mv_amc_path = config_path / Path("mv_amc_config.yaml")

    rectification_path = config_path / "rectification_config.yaml"
    rectification_config = None
    if config_params.model is not None:
        try:
            with open(rectification_path, "r") as file:
                rectification_config = yaml.load(file)
            rectification_config.setdefault("distortion", {})["model"] = config_params.model
            rectification_config.setdefault("param_space", {}).update(
                k1_min=config_params.k1_min,
                k1_max=config_params.k1_max,
                k1_step=config_params.k1_step,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Failed to read rectification configuration") from exc

    print(config_params.iterations)
    with open(camera_matrix_path, "r") as file:
        config_params_camera_matrix = yaml.load(file)
    config_params_camera_matrix["iterations"] = config_params.iterations
    with open(camera_matrix_path, "w") as file:
        yaml.dump(config_params_camera_matrix, file)

    with open(sv_amc_path, "r") as file:
        config_params_sv_amc = yaml.load(file)
    config_params_sv_amc["iterations"] = config_params.iterations
    with open(sv_amc_path, "w") as file:
        yaml.dump(config_params_sv_amc, file)

    with open(eval_path, "r") as file:
        config_params_eval = yaml.load(file)
    config_params_eval["layout_px_per_m"] = config_params.layout_px_per_m
    config_params_eval["virtual_gt_person_height"] = config_params.virtual_gt_person_height
    config_params_eval["virtual_gt_grid_spacing"] = config_params.virtual_gt_grid_spacing
    with open(eval_path, "w") as file:
        yaml.dump(config_params_eval, file)

    with open(mv_amc_path, "r") as file:    
        config_params_mv_amc = yaml.load(file)
    config_params_mv_amc["tracklet_filter"]["min_length"] = config_params.min_length
    config_params_mv_amc["tracklet_filter"]["min_moving_dist"] = config_params.min_moving_dist
    config_params_mv_amc["tracklet_filter"]["min_mean_moving_dist"] = config_params.min_mean_moving_dist
    config_params_mv_amc["min_matching_points"] = config_params.min_matching_points
    config_params_mv_amc["min_dtw_score_threshold"] = config_params.min_dtw_score_threshold
    config_params_mv_amc["min_distance_score_threshold"] = config_params.min_distance_score_threshold
    config_params_mv_amc["max_distance_score_hungarian_threshold"] = config_params.max_distance_score_hungarian_threshold
    config_params_mv_amc["min_matched_tracklets"] = config_params.min_matched_tracklets
    config_params_mv_amc["max_matched_tracklets_dist"] = config_params.max_matched_tracklets_dist
    config_params_mv_amc["min_tracklet_dist"] = config_params.min_tracklet_dist
    config_params_mv_amc["robust_two_view_calib"]["min_matched_points"] = config_params.min_matched_points
    config_params_mv_amc["robust_two_view_calib"]["inlier_threshold"] = config_params.inlier_threshold
    config_params_mv_amc["robust_two_view_calib"]["min_inliers"] = config_params.min_inliers
    config_params_mv_amc["robust_two_view_calib"]["reproj_err_threshold"] = config_params.reproj_err_threshold
    config_params_mv_amc["skip_frame"] = config_params.skip_frame
    if "vggt" not in config_params_mv_amc or not isinstance(config_params_mv_amc.get("vggt"), dict):
        config_params_mv_amc["vggt"] = {}
    vggt_cfg = config_params_mv_amc["vggt"]
    vggt_cfg["auto_select"] = config_params.vggt_auto_select
    vggt_cfg["timestamp_sec"] = config_params.timestamp_sec
    vggt_cfg["max_num_frames"] = config_params.vggt_max_num_frames
    vggt_cfg["confidence_score_threshold"] = config_params.vggt_confidence_score_threshold
    with open(mv_amc_path, "w") as file:
        yaml.dump(config_params_mv_amc, file)

    if rectification_config is not None:
        with open(rectification_path, "w") as file:
            yaml.dump(rectification_config, file)

    return {
        "code": 0,
        "message": "Config parameters updated successfully"
    }


@app.get(f"{API_VERSION_PREFIX}/config/defaults", tags=["Data"])
async def get_default_config_params():
    """
    Returns the true default configuration values.
    These never change and represent the original factory defaults.
    """
    default_config = {
        "iterations": 2000,
        "layout_px_per_m": 24.22,
        "virtual_gt_person_height": 1.75,
        "virtual_gt_grid_spacing": 1.0,
        "min_length": 90.0,
        "min_moving_dist": 2.0,
        "min_mean_moving_dist": 0.01,
        "min_matching_points": 100,
        "min_dtw_score_threshold": 25.0,
        "min_distance_score_threshold": 1.2,
        "max_distance_score_hungarian_threshold": 0.8,
        "min_matched_tracklets": 6,
        "max_matched_tracklets_dist": 2.0,
        "min_tracklet_dist": 3.0,
        "min_matched_points": 100,
        "inlier_threshold": 10.0,
        "min_inliers": 0.6,
        "reproj_err_threshold": 25.0,
        "skip_frame": 10,
        "timestamp_sec": 0,
        "vggt_auto_select": False,
        "vggt_max_num_frames": 10000,
        "vggt_confidence_score_threshold": 0.2,
        "model": "simple_divisional",
        "k1_min": -0.00001,
        "k1_max": 0.0,
        "k1_step": 100,
    }

    return {
        "code": 0,
        "config_params": default_config,
        "message": "Default config parameters retrieved successfully"
    }


@app.get(f"{API_VERSION_PREFIX}/config", tags=["Data"])
async def get_config_params():
    config_path = Path("../configs/config_AutoMagicCalib")
    camera_matrix_path = config_path / Path("camera_matrix_config.yaml")
    sv_amc_path = config_path / Path("sv_amc_config.yaml")
    eval_path = config_path / Path("eval_config.yaml")
    mv_amc_path = config_path / Path("mv_amc_config.yaml")

    try:
        # Read all config files
        with open(camera_matrix_path, "r") as file:
            camera_matrix_config = yaml.load(file)

        with open(sv_amc_path, "r") as file:
            sv_amc_config = yaml.load(file)
        
        with open(eval_path, "r") as file:
            eval_config = yaml.load(file)
        
        with open(mv_amc_path, "r") as file:
            mv_amc_config = yaml.load(file)

        try:
            with open(config_path / "rectification_config.yaml", "r") as file:
                rectification_config = yaml.load(file) or {}
        except FileNotFoundError:
            rectification_config = {}
        
        # Build response with current values (using actual YAML defaults as fallback)
        config_params = {
            "iterations": sv_amc_config.get("iterations", camera_matrix_config.get("iterations", 2000)),
            "layout_px_per_m": eval_config.get("layout_px_per_m", 24.22),
            "virtual_gt_person_height": eval_config.get("virtual_gt_person_height", 1.75),
            "virtual_gt_grid_spacing": eval_config.get("virtual_gt_grid_spacing", 1.0),
            "min_length": mv_amc_config.get("tracklet_filter", {}).get("min_length", 90.0),
            "min_moving_dist": mv_amc_config.get("tracklet_filter", {}).get("min_moving_dist", 2.0),
            "min_mean_moving_dist": mv_amc_config.get("tracklet_filter", {}).get("min_mean_moving_dist", 0.01),
            "min_matching_points": mv_amc_config.get("min_matching_points", 100),
            "min_dtw_score_threshold": mv_amc_config.get("min_dtw_score_threshold", 25.0),
            "min_distance_score_threshold": mv_amc_config.get("min_distance_score_threshold", 1.2),
            "max_distance_score_hungarian_threshold": mv_amc_config.get("max_distance_score_hungarian_threshold", 0.8),
            "min_matched_tracklets": mv_amc_config.get("min_matched_tracklets", 6),
            "max_matched_tracklets_dist": mv_amc_config.get("max_matched_tracklets_dist", 2.0),
            "min_tracklet_dist": mv_amc_config.get("min_tracklet_dist", 3.0),
            "min_matched_points": mv_amc_config.get("robust_two_view_calib", {}).get("min_matched_points", 100),
            "inlier_threshold": mv_amc_config.get("robust_two_view_calib", {}).get("inlier_threshold", 10.0),
            "min_inliers": mv_amc_config.get("robust_two_view_calib", {}).get("min_inliers", 0.6),
            "reproj_err_threshold": mv_amc_config.get("robust_two_view_calib", {}).get("reproj_err_threshold", 25.0),
            "skip_frame": mv_amc_config.get("skip_frame", 10),
            "timestamp_sec": mv_amc_config.get("vggt", {}).get("timestamp_sec", 0),
            "vggt_auto_select": mv_amc_config.get("vggt", {}).get("auto_select", False),
            "vggt_max_num_frames": mv_amc_config.get("vggt", {}).get("max_num_frames", 10000),
            "vggt_confidence_score_threshold": mv_amc_config.get("vggt", {}).get(
                "confidence_score_threshold", 0.2
            ),
            "model": rectification_config.get("distortion", {}).get("model", "simple_divisional"),
            "k1_min": rectification_config.get("param_space", {}).get("k1_min", -0.00001),
            "k1_max": rectification_config.get("param_space", {}).get("k1_max", 0.0),
            "k1_step": rectification_config.get("param_space", {}).get("k1_step", 100),
        }
        
        return {
            "code": 0,
            "config_params": config_params,
            "message": "Config parameters retrieved successfully"
        }
    except Exception as e:
        logger.error(f"Failed to read config files: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read config files: {e}")


'''
@app.post(f"{API_VERSION_PREFIX}/config/{{project_id}}", tags=["Data"])
async def update_config_params(project_id: str, config_params: ConfigParams) -> StandardResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if (app.state.projects[project_id]["project_state"] == "RUNNING") or (app.state.running_calibration == True):
        raise HTTPException(status_code = 400, detail = "Calibration is running. Updating config parameters is not allowed.")
        # return {"message": "Calibration is running. Updating config parameters is not allowed."}
    config_path = Path("../configs/config_AutoMagicCalib")
    camera_matrix_path = config_path / Path("camera_matrix_config.yaml")
    eval_path = config_path / Path("eval_config.yaml")
    mv_amc_path = config_path / Path("mv_amc_config.yaml")

    update_params(camera_matrix_path, "iterations", config_params.iterations)
    update_params(eval_path, "layout_px_per_m", config_params.layout_px_per_m)

    update_params(mv_amc_path, "min_length", config_params.min_length)
    update_params(mv_amc_path, "min_moving_dist", config_params.min_moving_dist)
    update_params(mv_amc_path, "min_mean_moving_dist", config_params.min_mean_moving_dist)
    update_params(mv_amc_path, "min_matching_points", config_params.min_matching_points)
    update_params(mv_amc_path, "min_dtw_score_threshold", config_params.min_dtw_score_threshold)
    update_params(mv_amc_path, "min_distance_score_threshold", config_params.min_distance_score_threshold)
    update_params(mv_amc_path, "max_distance_score_hungarian_threshold", config_params.max_distance_score_hungarian_threshold)
    update_params(mv_amc_path, "min_matched_tracklets", config_params.min_matched_tracklets)
    update_params(mv_amc_path, "max_matched_tracklets_dist", config_params.max_matched_tracklets_dist)
    update_params(mv_amc_path, "min_tracklet_dist", config_params.min_tracklet_dist)
    update_params(mv_amc_path, "min_matched_points", config_params.min_matched_points)
    update_params(mv_amc_path, "inlier_threshold", config_params.inlier_threshold)
    update_params(mv_amc_path, "min_inliers", config_params.min_inliers)
    update_params(mv_amc_path, "reproj_err_threshold", config_params.reproj_err_threshold)
    update_params(mv_amc_path, "skip_frame", config_params.skip_frame)
    update_params(mv_amc_path, "timestamp_sec", config_params.timestamp_sec)
    return StandardResponse(
        code=0,
        message="Config parameters updated successfully"
    )
'''

# Verify uploaded files
def _verify_project(project_id: str):
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Project verification is not allowed.")
        # return {"message": "Calibration is running. Project verification is not allowed."}
    if app.state.projects[project_id]["video_files_count"] < 1:
        raise HTTPException(status_code = 400, detail = "At least 1 video file is required for the calibration")
        # return {"message": "At least 1 video file is required for the calibration"}
    else:
        upload_path = Path(app.state.projects[project_id]["project_dir"])
        if app.state.projects[project_id]["video_directory"] is not None:
            upload_path = Path(app.state.projects[project_id]["video_directory"])
        for count in range(app.state.projects[project_id]["video_files_count"]):
            video_name = f"cam_{count:02d}.mp4"
            video_path = upload_path / Path(video_name)
            if not video_path.exists():
                raise HTTPException(status_code = 400, detail = f"Video file {video_name} missing, please reupload")
                # return {"message": f"Video file {video_name} missing"}
            if not video_name in app.state.projects[project_id]["video_files"]:
                raise HTTPException(status_code = 400, detail = f"Video file {video_name} not in the project list, check video naming convention")
                # return {"message": f"Video file {video_name} not in the project list, check video naming convention"}
            if not video_path.stat().st_size > 0:
                raise HTTPException(status_code = 400, detail = f"Video file {video_name} is empty, please reupload")
                # return {"message": f"Video file {{video_name}} is empty"}
            display_name = (
                app.state.projects[project_id].get("camera_name_map", {}).get(f"cam_{count:02d}")
                or video_name
            )
            try:
                _require_1080p_video(video_path, display_name=display_name)
            except ValueError as exc:
                logger.error(
                    "Project %s verification failed on video %s: %s",
                    project_id,
                    video_name,
                    exc,
                )
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        logger.info(
            "Project %s: all %d video(s) verified at %dx%d",
            project_id,
            app.state.projects[project_id]["video_files_count"],
            REQUIRED_VIDEO_WIDTH,
            REQUIRED_VIDEO_HEIGHT,
        )

    reconcile_alignment_artifacts(project_id)
    if not app.state.projects[project_id]["layout_file"]:
        raise HTTPException(status_code = 400, detail = "Layout file is required")
        # return {"message": "Layout file is required"}
    manual_layout, runtime_layout = _layout_artifact_paths(
        Path(app.state.projects[project_id]["project_dir"])
    )
    if not _is_non_empty_file(manual_layout):
        raise HTTPException(
            status_code=400,
            detail=f"Layout file is missing on disk ({manual_layout.as_posix()}). Please re-upload it.",
        )
    if not _is_non_empty_file(runtime_layout):
        raise HTTPException(
            status_code=500,
            detail=f"Failed to stage layout file at {runtime_layout.as_posix()}",
        )

    # TODO: Improve groundtruth file verification
    if app.state.projects[project_id]["groundtruth_file"]:
        gt_dir = Path(app.state.projects[project_id]["project_dir"]) / Path("groundtruth")
        if not gt_dir.exists():
            raise HTTPException(status_code = 400, detail = "Groundtruth directory not found")
            # return {"message": "Groundtruth directory not found"}
        if not gt_dir.is_dir():
            raise HTTPException(status_code = 400, detail = "Groundtruth directory is not a directory")
            # return {"message": "Groundtruth directory is not a directory"}
        # check ground_truth.json and calibration.json files
        if not os.path.exists(gt_dir / "ground_truth.json"):
            raise HTTPException(status_code = 400, detail = "Ground truth file not found")
        if not os.path.exists(gt_dir / "calibration.json"):
            raise HTTPException(status_code = 400, detail = "Calibration file not found")

    focal_length_size = len(app.state.projects[project_id]["focal_length"])
    if (focal_length_size > 0) and (focal_length_size != app.state.projects[project_id]["video_files_count"]):
        raise HTTPException(status_code = 400, detail = "Focal length count should be same as number of video files uploaded")
        # return {"message": "Focal lenght count should be same as number of video files uploaded"}

    project = app.state.projects[project_id]
    project["verified"] = True
    enable_amc_if_eligible(project)
    enable_vggt_if_eligible(project)
    refresh_project_state(project)


@app.post(f"{API_VERSION_PREFIX}/verify_project/{{project_id}}", tags=["Data"])
async def verify_project(project_id: str) -> VerifyProjectResponse:
    _verify_project(project_id)
    return VerifyProjectResponse(
        code=0,
        project_state=app.state.projects[project_id]["project_state"],
        message="Uploaded files verified successfully"
    )


#---------Annotation (ROI/Tripwire) API-----------------------------------------------------------#

def _require_annotation_target(project_id: str, video_name: str) -> None:
    """Reject annotation buckets that cannot map to current project media."""
    if (
        video_name != GLOBAL_ROIS_TRIPWIRES_KEY
        and video_name not in app.state.projects[project_id].get("video_files", [])
    ):
        raise HTTPException(status_code=404, detail="Camera/video not found in project")


# Get a video frame
@app.get(f"{API_VERSION_PREFIX}/video_frame/{{project_id}}/{{video_name}}/{{frame_index}}", tags=["Annotation"])
async def get_video_frame(project_id: str, video_name: str, frame_index: int, use_rectified: bool = False) -> Response:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if video_name not in app.state.projects[project_id]["video_files"]:
        raise HTTPException(status_code = 404, detail = "Video file not found")
        # return {"message": "Video file not found"}
    if frame_index < 0:
        raise HTTPException(status_code = 400, detail = "Invalid frame index")
        # return {"message": "Invalid frame index"}
    
    # Determine video path based on use_rectified flag
    if use_rectified:
        # Use rectified video from calibration results
        cam_num = video_name.replace('.mp4', '')  # e.g., "cam_00"
        video_path = Path(app.state.projects[project_id]["project_dir"]) / "output" / "single_view_results" / cam_num / "rectified.mp4"
    else:
        # Use original video
        if app.state.projects[project_id]["video_directory"] is not None:
            video_path = Path(app.state.projects[project_id]["video_directory"]) / Path(video_name)
        else:
            video_path = Path(app.state.projects[project_id]["project_dir"]) / Path(video_name)
    
    if not video_path.exists():
        raise HTTPException(status_code = 404, detail = "Video file not found")
        # return {"message": "Video file not found"}
    try:
        cap = open_video_capture(str(video_path))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ret, frame = cap.read()
        cap.release()
        return Response(content = cv2.imencode(".png", frame)[1].tobytes())
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to get video frame: {e}")
        # return {"message": f"Failed to get video frame: {e}"}


# Save annotations for a video
@app.post(f"{API_VERSION_PREFIX}/annotations/{{project_id}}/{{video_name}}", tags=["Annotation"])
async def save_annotations(
    project_id: str, 
    video_name: str, 
    request: Request
) -> StandardResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(
            status_code=400,
            detail="Calibration is running. Saving annotations is not allowed.",
        )
    _require_annotation_target(project_id, video_name)
    
    project_dir = Path(app.state.projects[project_id]["project_dir"])
    storage = AnnotationStorage(project_dir)
    
    try:
        # Parse JSON body
        body = await request.json()
        rois = body.get('rois', [])
        tripwire_lines = body.get('tripwireLines', [])
        tripwire_directions = body.get('tripwireDirections', [])
        
        # Save with 3 separate arrays
        storage.save_annotations(video_name, rois, tripwire_lines, tripwire_directions)
        return StandardResponse(
            code=0,
            message=f"Annotations saved successfully for {{video_name}}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save annotations: {e}")


# Get annotations for a video
@app.get(f"{API_VERSION_PREFIX}/annotations/{{project_id}}/{{video_name}}", tags=["Annotation"])
async def get_annotations(
    project_id: str,
    video_name: str,
    use_alignment_data: bool = False,
    include_global_projection: bool = True,
    result_type: Optional[str] = None,
) -> AnnotationsResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    _require_annotation_target(project_id, video_name)
    if result_type is not None and result_type not in ("amc", "vggt"):
        raise HTTPException(status_code=400, detail="result_type must be 'amc' or 'vggt'")
    
    project_dir = Path(app.state.projects[project_id]["project_dir"])
    
    try:
        # Load from alignment_data folder if requested
        if use_alignment_data:
            annotations_file = project_dir / "output" / "alignment_data" / "annotations.json"
            if not annotations_file.exists():
                return AnnotationsResponse(
                    code=0,
                    annotations={'rois': [], 'tripwireLines': [], 'tripwireDirections': []},
                    message="Annotations file not found"
                )
            
            with open(annotations_file, 'r') as f:
                all_annotations = json.load(f)

            annotations = resolve_camera_annotation_block(all_annotations, video_name)
        else:
            # Use regular AnnotationStorage (primary location)
            storage = AnnotationStorage(project_dir)
            annotations = storage.load_annotations(video_name)

        # Results / verification: merge projected global layout overlays.
        # Parameters editing: pass include_global_projection=false for raw camera drawings only.
        # result_type selects AMC vs VGGT camera matrices when projecting globals.
        if include_global_projection and video_name != GLOBAL_ROIS_TRIPWIRES_KEY:
            er, el, ed = compute_global_layout_camera_overlay(
                project_dir,
                app.state.projects[project_id],
                video_name,
                result_type=result_type,
            )
            annotations = merge_global_projected_overlay(annotations, er, el, ed)

        return AnnotationsResponse(
            code=0,
            annotations=annotations,
            message="Annotations retrieved successfully"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load annotations: {e}")


# Get all annotations for project
@app.get(f"{API_VERSION_PREFIX}/annotations/{{project_id}}", tags=["Annotation"])
async def get_all_annotations(project_id: str) -> AnnotationsResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    
    project_dir = Path(app.state.projects[project_id]["project_dir"])
    storage = AnnotationStorage(project_dir)
    
    try:
        all_annotations = storage.get_all_video_annotations()
        return AnnotationsResponse(
            code=0,
            annotations=all_annotations,
            message="All annotations retrieved successfully"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load annotations: {e}")


# Delete annotations for a video
@app.delete(f"{API_VERSION_PREFIX}/annotations/{{project_id}}/{{video_name}}", tags=["Annotation"])
async def delete_annotations(project_id: str, video_name: str) -> StandardResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(
            status_code=400,
            detail="Calibration is running. Deleting annotations is not allowed.",
        )
    _require_annotation_target(project_id, video_name)
    
    project_dir = Path(app.state.projects[project_id]["project_dir"])
    storage = AnnotationStorage(project_dir)
    
    try:
        storage.delete_annotations(video_name)
        return StandardResponse(
            code=0,
            message=f"Annotations deleted successfully for {{video_name}}"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete annotations: {e}")


#---------Linear media / rectification API-----------------------------------------#

def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _rectification_config_path() -> Path:
    return _repo_root() / "configs/config_AutoMagicCalib/rectification_config.yaml"


def _mv_amc_config_path() -> Path:
    return _repo_root() / "configs/config_AutoMagicCalib/mv_amc_config.yaml"


async def _sync_cam_dir_for_project(project: dict) -> None:
    """Rewrite mv_amc_config.yaml cam_dir to match this project's camera count.

    Shared config is container-global; every job that consumes it must refresh
    cam_dir from video_files_count so Manual/Linear→VGGT (and N≠4) cannot keep
    a stale default or a previous project's camera list.
    """
    await update_config(str(_mv_amc_config_path()), int(project["video_files_count"]))


def _assert_no_rectification_conflicts(project_id: str) -> None:
    project = app.state.projects[project_id]
    if app.state.running_calibration:
        raise HTTPException(
            status_code=409,
            detail="Another calibration, post-processing, or rectification job is running",
        )
    if project.get("rectification_state") == "RUNNING":
        raise HTTPException(status_code=409, detail="Rectification is running")
    if project.get("project_state") == "RUNNING":
        raise HTTPException(status_code=409, detail="Calibration is running")
    if project.get("amc_state") == "RUNNING":
        raise HTTPException(status_code=409, detail="AMC calibration is running")
    if project.get("vggt_state") == "RUNNING":
        raise HTTPException(status_code=409, detail="VGGT calibration is running")
    if project.get("postprocess_state") == "RUNNING":
        raise HTTPException(status_code=409, detail="Post-processing is running")


def _reset_calibration_outputs(project_id: str) -> None:
    """Remove derived media/results before a new rectification run."""
    _assert_no_rectification_conflicts(project_id)
    project = app.state.projects[project_id]
    project_dir = Path(project["project_dir"])
    output_dir = project_dir / "output"
    alignment_data_dir = output_dir / "alignment_data"
    # These are original-space user inputs.  The rectification job transforms
    # them into fresh rectified-space alignment/annotation files.  Never keep
    # the derived files here: a different optical transform would make them
    # stale and geometrically wrong.
    preserved_inputs = {}
    for filename in ("layout.png", "alignment_data_orig.json", "annotations_orig.json"):
        path = alignment_data_dir / filename
        if path.is_file():
            preserved_inputs[filename] = path.read_bytes()
    linear_backup = project_dir / ".linear_media_backup"
    if linear_backup.exists():
        shutil.rmtree(linear_backup)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    manual_params = project_dir / "manual_rectification_params.json"
    if manual_params.is_file():
        manual_params.unlink()
    if preserved_inputs:
        alignment_data_dir.mkdir(parents=True, exist_ok=True)
        for filename, content in preserved_inputs.items():
            (alignment_data_dir / filename).write_bytes(content)
    project["amc_state"] = "INIT"
    project["vggt_state"] = "INIT"
    project["postprocess_state"] = "INIT"
    project["amc_results_available"] = False
    project["rectification_state"] = "NOT_STARTED"
    project["rectification_path"] = None
    project["rectification_pid"] = None
    project["rectification_log"] = None
    reconcile_alignment_artifacts(project_id)
    refresh_project_state(project)


def _single_view_results_root(project_id: str) -> Path:
    return Path(app.state.projects[project_id]["project_dir"]) / "output" / "single_view_results"


def _project_video_dir(project: dict) -> Path:
    """Return the host path containing this project's uploaded source videos."""
    if project.get("video_directory"):
        return Path(project["video_directory"]).resolve()
    return Path(project["project_dir"]).resolve()


def _invalidate_linear_media(project_id: str) -> None:
    """Invalidate every derived artifact whenever source media changes."""
    project = app.state.projects[project_id]
    if project.get("rectification_state") == "RUNNING":
        raise HTTPException(status_code=409, detail="Rectification is running")
    project["rectification_state"] = "NOT_STARTED"
    project["rectification_path"] = None
    project["rectification_pid"] = None
    output_root = Path(project["project_dir"]) / "output"
    if output_root.exists():
        shutil.rmtree(output_root)
    manual_params = Path(project["project_dir"]) / "manual_rectification_params.json"
    if manual_params.is_file():
        manual_params.unlink()
    # Rectified points were drawn on media that no longer exists, and no
    # transform can map them onto the new source videos.  Drop them instead of
    # leaving a flag that claims alignment is still usable.
    manual_alignment, _, _ = _alignment_artifact_paths(Path(project["project_dir"]))
    if manual_alignment.exists():
        manual_alignment.unlink()
    project["verified"] = False
    project["amc_state"] = "INIT"
    project["vggt_state"] = "INIT"
    project["postprocess_state"] = "INIT"
    project["amc_results_available"] = False
    project["calibration_pid"] = None
    project["vggt_calibration_pid"] = None
    project["postprocess_pid"] = None
    reconcile_alignment_artifacts(project_id)
    refresh_project_state(project)


def _invalidate_calibration_state(project_id: str) -> None:
    """Invalidate results when a verified calibration input changes.

    Keep completed rectification and its staged media: these inputs do not
    change the optical transform. Existing files remain only as overwritten
    run artifacts; pipeline state is the validity gate for API/UI access.
    """
    project = app.state.projects[project_id]
    project["verified"] = False
    project["amc_state"] = "INIT"
    project["vggt_state"] = "INIT"
    project["postprocess_state"] = "INIT"
    project["amc_results_available"] = False
    refresh_project_state(project)


def _sync_alignment_after_linear_media(project_id: str) -> None:
    """Materialize rectified alignment from _orig once linear media is ready.

    Covers auto-commit, manual rectification, and already-linear staging. Uses
    committed distortion.yaml first (manual/auto), then GeoCalib estimates, then
    identity when no distortion file exists.
    """
    project = app.state.projects[project_id]
    output_dir = Path(project["project_dir"]) / "output"
    orig_path = output_dir / "alignment_data" / "alignment_data_orig.json"
    if not orig_path.is_file():
        return
    try:
        materialized = materialize_rectified_alignment(
            output_dir,
            is_single_cam=project["video_files_count"] == 1,
        )
        manual_path = Path(project["project_dir"]) / "manual_adjustment" / "alignment_data.json"
        manual_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(materialized, manual_path)
        project["alignment_file"] = True
        logger.info(
            "Materialized rectified alignment for project %s after linear media completed",
            project_id,
        )
    except Exception:
        logger.exception(
            "Failed to materialize rectified alignment for project %s after linear media",
            project_id,
        )


def _linear_media_missing(project_id: str) -> List[str]:
    """Return missing artifacts from the E2E linear-media contract."""
    project = app.state.projects[project_id]
    output_root = Path(project["project_dir"]) / "output" / "single_view_results"
    missing = []
    for index in range(project["video_files_count"]):
        camera_dir = output_root / f"cam_{index:02d}"
        for filename in ("rectified.mp4", "rectified.jpg"):
            path = camera_dir / filename
            if not path.is_file() or path.stat().st_size == 0:
                missing.append(str(path))
    return missing


def _auto_estimates_missing(project_id: str) -> List[str]:
    """Return missing GeoCalib parameter files for the estimate-only auto step."""
    project = app.state.projects[project_id]
    output_root = Path(project["project_dir"]) / "output" / "single_view_results"
    missing = []
    for index in range(project["video_files_count"]):
        path = (
            output_root
            / f"cam_{index:02d}"
            / "distortion_estimation"
            / "geocalib_distortion.yaml"
        )
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(str(path))
    return missing


def _mark_rectification_ready_to_commit(project: dict) -> None:
    """Block downstream stages while preserving estimates/committed drafts."""
    project["amc_state"] = "INIT"
    project["vggt_state"] = "INIT"
    project["postprocess_state"] = "INIT"
    project["amc_results_available"] = False
    project["rectification_state"] = "READY_FOR_REVIEW"
    project["rectification_pid"] = None
    refresh_project_state(project)


def _stage_already_linear_media(project_id: str) -> None:
    """Stage already-linear uploads under the stable E2E artifact names.

    Clears any prior distortion caches so Linear means identity media.
    """
    project = app.state.projects[project_id]
    source_dir = _project_video_dir(project)
    output_root = Path(project["project_dir"]) / "output" / "single_view_results"
    # Wipe prior auto/manual distortion artifacts before staging identity media.
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    params_path = Path(project["project_dir"]) / "manual_rectification_params.json"
    if params_path.is_file():
        params_path.unlink()

    for index in range(project["video_files_count"]):
        camera_name = f"cam_{index:02d}"
        source_video = (source_dir / f"{camera_name}.mp4").resolve()
        if not source_video.is_file():
            raise FileNotFoundError(f"Source video not found: {source_video}")

        camera_dir = output_root / camera_name
        camera_dir.mkdir(parents=True, exist_ok=True)
        staged_video = camera_dir / "rectified.mp4"
        if staged_video.exists() or staged_video.is_symlink():
            staged_video.unlink()
        staged_video.symlink_to(source_video)

        capture = open_video_capture(source_video.as_posix())
        try:
            ok, frame = capture.read()
        finally:
            capture.release()
        if not ok or frame is None:
            raise RuntimeError(f"Could not extract a linear preview frame from {source_video}")
        if not cv2.imwrite((camera_dir / "rectified.jpg").as_posix(), frame):
            raise RuntimeError(f"Could not write rectified.jpg for {camera_name}")
        if not cv2.imwrite((camera_dir / "background.jpg").as_posix(), frame):
            raise RuntimeError(f"Could not write background.jpg for {camera_name}")


def enable_vggt_if_eligible(project: dict) -> None:
    """VGGT depends on staged linear media, not on AMC/E2E completion."""
    if not project.get("verified") or project.get("video_files_count", 0) < 2:
        return
    if project.get("rectification_state") != "COMPLETED":
        return
    state = project.get("vggt_state")
    # Do not overwrite a running, completed, or failed calibration. For a
    # project waiting to run, model availability is part of readiness.
    if state not in ("INIT", "READY", "MODEL_MISSING"):
        return
    if not app.state.vggt_available:
        project["vggt_state"] = "MODEL_MISSING"
        return
    project["vggt_state"] = "READY"


def _write_rectification_provenance_log(
    project_id: str,
    path: str,
    log_handle=None,
    *,
    replace: bool = False,
) -> None:
    """Record the committed per-project transform in a human-readable log block."""
    project = app.state.projects[project_id]
    owns_handle = log_handle is None
    if owns_handle:
        log_path = Path(project["project_dir"]) / "rectification.log"
        log_handle = open(log_path, "w" if replace else "a")
    try:
        log_handle.write("\n=== Rectification provenance ===\n")
        log_handle.write(f"Path: {path}\n")
        if path == "linear":
            log_handle.write("Transform: identity (videos were already rectified)\n")
            for index in range(project["video_files_count"]):
                log_handle.write(
                    f"cam_{index:02d}: already linear; no distortion model applied\n"
                )
        else:
            output_root = _single_view_results_root(project_id)
            for index in range(project["video_files_count"]):
                camera_id = f"cam_{index:02d}"
                params_path = output_root / camera_id / "distortion.yaml"
                if not params_path.is_file():
                    log_handle.write(
                        f"{camera_id}: committed distortion parameters unavailable\n"
                    )
                    continue
                with open(params_path) as params_file:
                    params = yaml.load(params_file) or {}
                fields = [f"model={params.get('model', 'unknown')}"]
                for name in ("k1", "k2"):
                    if name in params:
                        fields.append(f"{name}={params[name]}")
                log_handle.write(f"{camera_id}: {', '.join(fields)}\n")
        log_handle.write("=== End rectification provenance ===\n")
        log_handle.flush()
    finally:
        if owns_handle:
            log_handle.close()


async def monitor_rectification_completion(project_id: str, process: subprocess.Popen):
    return_code = await asyncio.get_event_loop().run_in_executor(None, process.wait)
    project = app.state.projects[project_id]
    log_handle = project.get("rectification_log")
    if log_handle is not None:
        log_handle.close()
    project["rectification_log"] = None
    project["rectification_pid"] = None
    if return_code == 0 and not _auto_estimates_missing(project_id):
        _mark_rectification_ready_to_commit(project)
    else:
        project["rectification_state"] = "ERROR"
        refresh_project_state(project)


@app.post(f"{API_VERSION_PREFIX}/rectification/{{project_id}}", tags=["Rectification"])
async def start_rectification(
    project_id: str, background_tasks: BackgroundTasks
) -> RectificationResponse:
    """Estimate frame-0 distortion for every camera; full media waits for commit."""
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    project = app.state.projects[project_id]
    _assert_no_rectification_conflicts(project_id)
    _reset_calibration_outputs(project_id)
    project = app.state.projects[project_id]
    project["rectification_path"] = "auto"
    if project["video_files_count"] < 1:
        raise HTTPException(status_code=400, detail="At least one video is required")

    await update_config(
        str(_mv_amc_config_path()),
        project["video_files_count"],
    )
    script_dir = Path(__file__).resolve().parent.parent / "service_scripts"
    output_dir = (Path(project["project_dir"]) / "output").resolve()
    command = [
        "bash",
        "launch_Rectification.sh",
        "-v",
        _project_video_dir(project).as_posix(),
        "-o",
        output_dir.as_posix(),
        "-m",
        "local",
    ]
    log_path = Path(project["project_dir"]) / "rectification.log"
    log_handle = open(log_path, "w")
    log_handle.write(f"Running command: {' '.join(command)}\n")
    log_handle.flush()
    try:
        process = subprocess.Popen(
            command,
            cwd=script_dir,
            text=True,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
    except Exception:
        log_handle.close()
        raise

    project["rectification_state"] = "RUNNING"
    project["rectification_pid"] = process.pid
    project["rectification_log"] = log_handle
    refresh_project_state(project)
    background_tasks.add_task(monitor_rectification_completion, project_id, process)
    return RectificationResponse(
        code=0,
        message="Auto-estimation started on frame 0.",
        rectification_state="RUNNING",
        rectification_pid=process.pid,
    )


@app.post(f"{API_VERSION_PREFIX}/linear_media/{{project_id}}", tags=["Rectification"])
async def stage_already_linear_media(project_id: str) -> RectificationResponse:
    """Explicit UI action for uploads that are already linear."""
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    project = app.state.projects[project_id]
    if project["video_files_count"] == 0:
        raise HTTPException(
            status_code=400,
            detail="Videos are required before staging linear media.",
        )
    _assert_no_rectification_conflicts(project_id)
    had_derived_output = (Path(project["project_dir"]) / "output").exists()
    _reset_calibration_outputs(project_id)
    project = app.state.projects[project_id]
    try:
        await _sync_cam_dir_for_project(project)
        _stage_already_linear_media(project_id)
    except Exception as exc:
        project["rectification_state"] = "ERROR"
        raise HTTPException(status_code=500, detail=f"Failed to stage linear media: {exc}")
    project["rectification_state"] = "COMPLETED"
    project["rectification_path"] = "linear"
    _write_rectification_provenance_log(
        project_id, "linear", replace=True
    )
    reconcile_alignment_artifacts(project_id)
    enable_amc_if_eligible(project)
    enable_vggt_if_eligible(project)
    refresh_project_state(project)
    message = (
        "Already-linear media staged successfully. Previous derived calibration output "
        "was cleared; continue with alignment and calibration."
        if had_derived_output
        else "Already-linear media staged successfully. Continue with alignment."
    )
    return RectificationResponse(
        code=0,
        message=message,
        rectification_state="COMPLETED",
        rectification_pid=None,
    )


@app.get(
    f"{API_VERSION_PREFIX}/rectification/{{project_id}}/cameras",
    tags=["Rectification"],
)
async def get_rectification_cameras(project_id: str) -> RectificationCamerasResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    project = app.state.projects[project_id]
    if project["video_files_count"] < 1:
        raise HTTPException(status_code=400, detail="At least one video is required")
    video_dir = _project_video_dir(project)
    output_root = _single_view_results_root(project_id)
    output_root.mkdir(parents=True, exist_ok=True)
    cameras = build_camera_states(video_dir, output_root, project["video_files"])
    return RectificationCamerasResponse(
        code=0,
        message="Rectification camera metadata",
        cameras=[RectificationCameraState(**cam) for cam in cameras],
    )


@app.post(
    f"{API_VERSION_PREFIX}/rectification/{{project_id}}/preview/{{camera_id}}",
    tags=["Rectification"],
    response_class=Response,
    responses={
        200: {
            "description": "PNG image",
            "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def preview_manual_rectification_frame(
    project_id: str,
    camera_id: str,
    body: ManualRectificationPreviewRequest,
) -> Response:
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    project = app.state.projects[project_id]
    video_name = f"{camera_id}.mp4"
    if video_name not in project["video_files"]:
        raise HTTPException(status_code=404, detail="Camera not found")
    video_path = _project_video_dir(project) / video_name
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="Source video not found")
    try:
        png = preview_rectified_frame_png(
            video_path,
            body.model_dump(),
            _rectification_config_path(),
            frame_index=0,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Preview failed: {exc}")
    return Response(content=png, media_type="image/png")


@app.post(
    f"{API_VERSION_PREFIX}/rectification/{{project_id}}/manual/start",
    tags=["Rectification"],
)
async def start_manual_rectification(project_id: str) -> RectificationResponse:
    """Enter manual drafting and block downstream stages until explicit commit."""
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    project = app.state.projects[project_id]
    _assert_no_rectification_conflicts(project_id)
    if project["video_files_count"] < 1:
        raise HTTPException(status_code=400, detail="At least one video is required")
    await _sync_cam_dir_for_project(project)
    project["rectification_path"] = "manual"
    _mark_rectification_ready_to_commit(project)
    return RectificationResponse(
        code=0,
        message="Manual rectification ready. Tune parameters, then generate videos.",
        rectification_state="READY_FOR_REVIEW",
        rectification_pid=None,
    )


async def monitor_manual_rectification_completion(
    project_id: str, process: subprocess.Popen
):
    return_code = await asyncio.get_event_loop().run_in_executor(None, process.wait)
    project = app.state.projects[project_id]
    log_handle = project.get("rectification_log")
    if return_code == 0 and not _linear_media_missing(project_id):
        project["rectification_state"] = "COMPLETED"
        path = project.get("rectification_path") or "manual"
        if log_handle is not None:
            try:
                _write_rectification_provenance_log(project_id, path, log_handle)
            except Exception:
                logger.exception(
                    "Failed to append rectification provenance for project %s",
                    project_id,
                )
        reconcile_alignment_artifacts(project_id)
        enable_amc_if_eligible(project)
        enable_vggt_if_eligible(project)
        refresh_project_state(project)
    else:
        project["rectification_state"] = "ERROR"
        refresh_project_state(project)
    if log_handle is not None:
        log_handle.close()
    project["rectification_log"] = None
    project["rectification_pid"] = None


@app.post(f"{API_VERSION_PREFIX}/rectification/{{project_id}}/manual", tags=["Rectification"])
async def commit_manual_rectification(
    project_id: str,
    body: ManualRectificationCommitRequest,
    background_tasks: BackgroundTasks,
) -> RectificationResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    project = app.state.projects[project_id]
    _assert_no_rectification_conflicts(project_id)
    if project["video_files_count"] < 1:
        raise HTTPException(status_code=400, detail="At least one video is required")
    expected = {f"cam_{i:02d}" for i in range(project["video_files_count"])}
    if set(body.cameras.keys()) != expected:
        raise HTTPException(
            status_code=400,
            detail=f"Expected camera params for {sorted(expected)}",
        )

    rectification_path = project.get("rectification_path")
    if rectification_path not in ("auto", "manual"):
        rectification_path = "manual"
    _reset_calibration_outputs(project_id)
    project = app.state.projects[project_id]
    project["rectification_path"] = rectification_path
    try:
        await _sync_cam_dir_for_project(project)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to update camera config: {exc}"
        )
    # Resolve all paths: subprocess cwd is core/rectification, so relative
    # project_dir values like "projects/..." would miss the params JSON.
    output_root = _single_view_results_root(project_id).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    params_path = (Path(project["project_dir"]) / "manual_rectification_params.json").resolve()
    params_payload = {
        cam_id: cam.model_dump() for cam_id, cam in body.cameras.items()
    }
    with open(params_path, "w") as f:
        json.dump(params_payload, f)

    script = (_repo_root() / "core" / "rectification" / "manual_rectify_commit.py").resolve()
    python_bin = sys.executable
    command = [
        python_bin,
        str(script),
        "-v",
        _project_video_dir(project).as_posix(),
        "-o",
        output_root.as_posix(),
        "-c",
        _rectification_config_path().resolve().as_posix(),
        "-p",
        params_path.as_posix(),
    ]
    log_path = (Path(project["project_dir"]) / "rectification.log").resolve()
    log_handle = open(log_path, "w")
    log_handle.write(f"Running command: {' '.join(command)}\n")
    log_handle.flush()
    try:
        process = subprocess.Popen(
            command,
            cwd=str((_repo_root() / "core" / "rectification").resolve()),
            text=True,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
    except Exception:
        log_handle.close()
        project["rectification_state"] = "ERROR"
        raise

    project["rectification_state"] = "RUNNING"
    project["rectification_pid"] = process.pid
    project["rectification_log"] = log_handle
    refresh_project_state(project)
    background_tasks.add_task(
        monitor_manual_rectification_completion, project_id, process
    )
    return RectificationResponse(
        code=0,
        message="Generating rectified videos",
        rectification_state="RUNNING",
        rectification_pid=process.pid,
    )


@app.get(f"{API_VERSION_PREFIX}/rectification/{{project_id}}", tags=["Rectification"])
async def get_rectification_status(project_id: str) -> RectificationResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    project = app.state.projects[project_id]
    state = project.get("rectification_state", "NOT_STARTED")
    return RectificationResponse(
        code=0,
        message=f"Rectification state: {state}",
        rectification_state=state,
        rectification_pid=project.get("rectification_pid"),
    )


@app.get(
    f"{API_VERSION_PREFIX}/rectification/{{project_id}}/single_view_results",
    tags=["Rectification"],
)
async def download_single_view_results(project_id: str) -> FileResponse:
    """Zip and download output/single_view_results after rectification completes."""
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    project = app.state.projects[project_id]
    if project.get("rectification_state") != "COMPLETED":
        raise HTTPException(
            status_code=400,
            detail="Rectification must be completed before downloading single-view results",
        )
    if _linear_media_missing(project_id):
        raise HTTPException(
            status_code=400,
            detail="single_view_results is incomplete; finish rectification first",
        )

    project_dir = Path(project["project_dir"])
    dir_to_archive = project_dir / "output" / "single_view_results"
    if not dir_to_archive.is_dir():
        raise HTTPException(status_code=404, detail="single_view_results directory not found")

    archive_name = "single_view_results.zip"
    archive_path = project_dir / "output" / archive_name
    try:
        shutil.make_archive(archive_path.with_suffix(""), "zip", dir_to_archive)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create {archive_name}: {exc}",
        ) from exc
    download_name = f"{project.get('project_name') or project_id}_single_view_results.zip"
    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=download_name,
    )


@app.get(
    f"{API_VERSION_PREFIX}/rectification/{{project_id}}/log",
    tags=["Rectification"],
)
async def get_rectification_log(project_id: str) -> FileResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    log_path = Path(app.state.projects[project_id]["project_dir"]) / "rectification.log"
    if not log_path.is_file():
        raise HTTPException(status_code=404, detail="Rectification log not found")
    return FileResponse(log_path, media_type="text/plain", filename="rectification.log")


@app.get(
    f"{API_VERSION_PREFIX}/rectification/{{project_id}}/log/stream",
    tags=["Rectification"],
)
async def stream_rectification_log(project_id: str) -> StreamingResponse:
    """Stream rectification.log in real time using Server-Sent Events."""
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    log_path = Path(app.state.projects[project_id]["project_dir"]) / "rectification.log"
    if not log_path.is_file():
        raise HTTPException(status_code=404, detail="Rectification log not found")

    async def log_generator():
        last_position = 0
        while True:
            try:
                with open(log_path, "r") as log_file:
                    log_file.seek(last_position)
                    new_content = log_file.read()
                    if new_content:
                        last_position = log_file.tell()
                        for line in new_content.splitlines():
                            yield f"data: {line}\n\n"

                state = app.state.projects.get(project_id, {}).get(
                    "rectification_state", ""
                )
                if state != "RUNNING":
                    yield f"data: [RECTIFICATION {state}]\n\n"
                    break
                await asyncio.sleep(0.5)
            except Exception as exc:
                yield f"data: [ERROR: {exc}]\n\n"
                break

    return StreamingResponse(
        log_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


#---------Calibration API-----------------------------------------------------------#

# Monitor the calibration completion
async def monitor_calibration_completion(
    project_id: str, process: subprocess.Popen, pipeline: str
):
    """Finalize one known calibration pipeline after its subprocess exits."""
    is_vggt = pipeline == "vggt"
    log_key = "vggt_calibration_log" if is_vggt else "calibration_log"
    try:
        return_code = await asyncio.get_event_loop().run_in_executor(None, process.wait)
    except Exception as e:
        log_file = app.state.projects[project_id].get(log_key)
        if log_file is not None:
            log_file.close()
            app.state.projects[project_id][log_key] = None
        raise HTTPException(status_code = 500, detail = f"Failed to monitor calibration completion: {e}")
        # return {"message": f"Failed to monitor calibration completion: {e}"}

    log_file = app.state.projects[project_id].get(log_key)
    if log_file is not None:
        log_file.close()
        app.state.projects[project_id][log_key] = None
    
    # Extract [INFO] [LATENCY] lines from calibration log and append to log file
    try:
        project_dir = Path(app.state.projects[project_id]["project_dir"])
        log_file_path = project_dir / "vggt_calibration.log" if is_vggt else project_dir / "calibration.log"
        
        if log_file_path.exists():
            latency_lines = []
            with open(log_file_path, "r") as f:
                for line in f:
                    if "[INFO] [LATENCY]" in line:
                        latency_lines.append(line.strip())
            
            # Append latency summary to log file
            if latency_lines:
                with open(log_file_path, "a") as f:
                    f.write("\n\n=== LATENCY SUMMARY ===\n")
                    for line in latency_lines:
                        f.write(f"{line}\n")
                logger.info(f"Extracted {len(latency_lines)} latency entries for project {project_id}")
            
                # Create a new file with the latency summary
                latency_summary_file = project_dir / "vggt_calibration_latency.csv" if is_vggt \
                    else project_dir / "calibration_latency.csv"
                with open(latency_summary_file, "w") as f:
                    for line in latency_lines:
                        line = line.replace("[INFO] [LATENCY]", "")
                        line = line.replace("ms", "")
                        line = line.replace(":", ",")
                        elements = line.split(",")
                        elements = elements[:-1] + [""] * 4 + [elements[-1]]
                        line = ",".join(elements)
                        f.write(f"{line}\n")
                logger.info(f"Created latency summary file: {latency_summary_file}")
    except Exception as e:
        logger.warning(f"Failed to extract latency info from calibration log: {e}")
    
    app.state.running_calibration = False
    
    if is_vggt:
        # VGGT process completed
        if return_code == 0:
            app.state.projects[project_id]["vggt_state"] = "COMPLETED"
            refresh_postprocess_state(app.state.projects[project_id], force_ready=True)
        else:
            app.state.projects[project_id]["vggt_state"] = "ERROR"
        app.state.projects[project_id]["vggt_calibration_pid"] = None
        refresh_project_state(app.state.projects[project_id])
        return {
            "code": return_code,
            "message": "VGGT calibration completed" if return_code == 0 else "VGGT calibration failed"
        }
    else:
        # AMC process completed
        if return_code == 0:
            app.state.projects[project_id]["amc_state"] = "COMPLETED"
            app.state.projects[project_id]["amc_results_available"] = check_amc_results_exist(
                app.state.projects[project_id]["project_dir"],
                app.state.projects[project_id].get("video_files_count", 0),
            )
            # VGGT is independent of AMC; enable whenever linear media is staged.
            enable_vggt_if_eligible(app.state.projects[project_id])
            refresh_postprocess_state(app.state.projects[project_id], force_ready=True)
            app.state.projects[project_id]["calibration_pid"] = None
            refresh_project_state(app.state.projects[project_id])
            
            output_path = Path(app.state.projects[project_id]["project_dir"]) / Path("output")
            
            # Deleting the intermedicate files not needed for the result
            try:        
                for dir in (output_path / Path("single_view_results")).glob("*"):
                    (dir / Path("peoplenet_out_2d.mp4")).unlink()
                    shutil.rmtree(dir / Path("kitti_detector"))
                eval_path = output_path / Path("multi_view_results") / Path("evaluation")
                if eval_path.exists():
                    for dir in eval_path.iterdir():
                        if dir.is_dir():
                            if re.match(r"[0-9]+_[0-9]+", dir.name):
                                shutil.rmtree(dir / Path("res_imgs"))

            except Exception as e:
                logger.warning(f"Failed to cleanup intermediate files: {e}")
            return {
                "code": 0,
                "message": "Calibration completed successfully"
            }
        else:
            app.state.projects[project_id]["amc_state"] = "ERROR"
            app.state.projects[project_id]["amc_results_available"] = check_amc_results_exist(
                app.state.projects[project_id]["project_dir"],
                app.state.projects[project_id].get("video_files_count", 0),
            )
            enable_vggt_if_eligible(app.state.projects[project_id])
            refresh_postprocess_state(app.state.projects[project_id], force_ready=True)
            app.state.projects[project_id]["calibration_pid"] = None
            refresh_project_state(app.state.projects[project_id])
            
            return {
                "code": 1,
                "message": "Calibration failed"
            }

# Function to update config file based on number of cameras, called inside the calibration API
async def update_config(file_path: str, num_cams: int):
    search_string = r'^cam_dir:.*'
    replace_string = "cam_dir: "
    cam_list = []
    for cam_id in range(num_cams):
        cam_string = f'cam_{cam_id:02d}'
        cam_list.append(cam_string)
    replace_string += str(cam_list)

    with open(Path(file_path), "r") as file:
        lines = file.readlines()
    for index, line in enumerate(lines):
        pattern = re.match(search_string, line)
        if pattern:
            result = re.sub(search_string, replace_string, line)
            lines[index] = result
    with open(file_path, "w") as file:
        file.writelines(lines)

# Run the calibration
@app.post(f"{API_VERSION_PREFIX}/calibrate/{{project_id}}", tags=["Calibration"])
async def run_bash_script(project_id: str, request: CalibrateRequest, background_tasks: BackgroundTasks) -> CalibrationResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    if app.state.running_calibration:
        raise HTTPException(status_code = 400, detail = "Calibration is already running. Please wait for it to complete.")
        # return {"message": "Calibration is already running. Please wait for it to complete."}
    if not app.state.projects[project_id].get("verified"):
        raise HTTPException(status_code=400, detail="Project is not verified. Verify common inputs first.")
    # READY after verify; COMPLETED/ERROR allow overwrite re-runs without relaunch.
    if app.state.projects[project_id]["amc_state"] not in ("READY", "COMPLETED", "ERROR"):
        raise HTTPException(status_code = 400, detail = "Project is not ready for calibration. Please verify the project first.")
        # return {"message": "Project is not ready for calibration. Please verify the project first."}
    if app.state.projects[project_id].get("rectification_state") != "COMPLETED":
        raise HTTPException(
            status_code=400,
            detail="Linear media is not ready. Start rectification or choose already-linear media first.",
        )
    missing_linear_media = _linear_media_missing(project_id)
    if missing_linear_media:
        raise HTTPException(
            status_code=400,
            detail=f"Missing staged linear media: {', '.join(missing_linear_media)}",
        )
    project_dir = app.state.projects[project_id]["project_dir"]
    
    # Update config file based on number of cameras
    try:
        app.state.running_calibration = True
        await _sync_cam_dir_for_project(app.state.projects[project_id])
    except Exception as e:
        app.state.running_calibration = False
        raise HTTPException(status_code = 500, detail = f"Failed to update config file: {e}")
        # return {"message": f"Failed to update config file: {e}"}

    # Build command with proper argument handling
    command = "bash launch_EndToEndCalib.sh"
    if app.state.projects[project_id]["video_directory"] is not None:
        command += f" -v {app.state.projects[project_id]['video_directory']}"
    else:
        command += f" -v ../server/{project_dir}"
    command += f" -o ../server/{project_dir}/output"
    if app.state.projects[project_id]["groundtruth_file"]:
        command += f" -g ../server/{project_dir}/groundtruth"
    command += f" -l ../server/{project_dir}/manual_adjustment/layout.png"
    if (len(app.state.projects[project_id]["focal_length"]) > 0):
        command += f" -f {','.join(str(focal_length) for focal_length in app.state.projects[project_id]['focal_length'])}"
    command += f" -d {request.detector_type}"
    command += " -m local"
    # Single-view calibration when only one camera is uploaded
    if app.state.projects[project_id]["video_files_count"] == 1:
        command += " --single-cam"

    # Run command
    log_file_path = Path(app.state.projects[project_id]["project_dir"]) / Path("calibration.log")
    log_file = open(log_file_path, "w")
    log_file.write(f"Running command: {command}\n")
    app.state.projects[project_id]["calibration_log"] = log_file
    app.state.projects[project_id]["project_state"] = "RUNNING"
    app.state.projects[project_id]["amc_state"] = "RUNNING"
    # Re-runs overwrite derived artifacts; require a fresh post-process afterward.
    app.state.projects[project_id]["postprocess_state"] = "INIT"
    try:
        # ret = subprocess.run(command, cwd="../service_scripts/")
        ret = subprocess.Popen(["bash", "-c", command], cwd="../service_scripts/", text=True, bufsize=1,
            stdout=log_file, stderr=subprocess.STDOUT, preexec_fn=os.setsid)
    except Exception as e:
        app.state.running_calibration = False
        app.state.projects[project_id]["project_state"] = "ERROR"
        app.state.projects[project_id]["amc_state"] = "ERROR"
        raise HTTPException(status_code = 500, detail = f"Failed to run calibration: {e}")
        # return {"message": f"Failed to run calibration: {e}"}
    app.state.projects[project_id]["calibration_pid"] = ret.pid
    
    # Add background task to monitor the calibration completion
    try:
        background_tasks.add_task(monitor_calibration_completion, project_id, ret, "amc")
    except Exception as e:
        app.state.running_calibration = False
        raise HTTPException(status_code = 500, detail = f"Failed to add background task to monitor calibration completion: {e}")
        # return {"message": f"Failed to add background task to monitor calibration completion: {e}"}

    return CalibrationResponse(
        code=0,
        project_state=app.state.projects[project_id]["project_state"],
        calibration_pid=app.state.projects[project_id]["calibration_pid"],
        message="Calibration started successfully"
    )

@app.post(f"{API_VERSION_PREFIX}/vggt/calibrate/{{project_id}}", tags=["Calibration"])
async def run_vggt_calibration(project_id: str, background_tasks: BackgroundTasks) -> CalibrationResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
    if app.state.running_calibration:
        raise HTTPException(status_code = 400, detail = "Calibration is already running. Please wait for it to complete.")
    project = app.state.projects[project_id]
    if not app.state.vggt_available:
        enable_vggt_if_eligible(project)
        raise HTTPException(
            status_code=400,
            detail="VGGT model is missing.",
        )
    if not project.get("verified"):
        raise HTTPException(status_code=400, detail="Project is not verified. Verify common inputs first.")
    if project.get("rectification_state") != "COMPLETED":
        raise HTTPException(
            status_code=400,
            detail="Linear media is not ready. Start rectification or choose already-linear media first.",
        )
    missing_linear_media = _linear_media_missing(project_id)
    if missing_linear_media:
        raise HTTPException(
            status_code=400,
            detail=f"Missing staged linear media: {', '.join(missing_linear_media)}",
        )
    # READY after verify/rectify; COMPLETED/ERROR allow overwrite re-runs.
    if project["vggt_state"] not in ("READY", "ERROR", "COMPLETED"):
        enable_vggt_if_eligible(project)
    if project["vggt_state"] not in ("READY", "ERROR", "COMPLETED"):
        raise HTTPException(
            status_code=400,
            detail="VGGT calibration is not ready. Verify the project and stage linear media first.",
        )
    if project["video_files_count"] == 1:
        raise HTTPException(status_code = 400, detail = "VGGT calibration is not supported for single-view calibration.")
    try:
        await _sync_cam_dir_for_project(project)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config file: {e}")
    project_dir = project["project_dir"]
    
    # Build VGGT command
    command = "bash launch_VggtCalib.sh"
    command += f" -a ../server/{project_dir}/output"
    command += f" -l ../server/{project_dir}/manual_adjustment/layout.png"
    if app.state.projects[project_id]["groundtruth_file"]:
        command += f" -g ../server/{project_dir}/groundtruth"
    command += " -m local"

    # Run command
    log_file_path = Path(app.state.projects[project_id]["project_dir"]) / Path("vggt_calibration.log")
    log_file = open(log_file_path, "w")
    log_file.write(f"Running command: {command}\n")
    app.state.projects[project_id]["vggt_calibration_log"] = log_file
    app.state.projects[project_id]["vggt_state"] = "RUNNING"
    app.state.projects[project_id]["project_state"] = "RUNNING"
    # Re-runs overwrite derived artifacts; require a fresh post-process afterward.
    app.state.projects[project_id]["postprocess_state"] = "INIT"
    app.state.running_calibration = True
    try:
        ret = subprocess.Popen(["bash", "-c", command], cwd="../service_scripts/", text=True, bufsize=1,
            stdout=log_file, stderr=subprocess.STDOUT, preexec_fn=os.setsid)
    except Exception as e:
        app.state.running_calibration = False
        app.state.projects[project_id]["vggt_state"] = "ERROR"
        app.state.projects[project_id]["project_state"] = "ERROR"
        raise HTTPException(status_code = 500, detail = f"Failed to run VGGT calibration: {e}")
    app.state.projects[project_id]["vggt_calibration_pid"] = ret.pid
    
    # Add background task to monitor the calibration completion
    try:
        background_tasks.add_task(monitor_calibration_completion, project_id, ret, "vggt")
    except Exception as e:
        app.state.running_calibration = False
        raise HTTPException(status_code = 500, detail = f"Failed to add background task to monitor calibration completion: {e}")

    return CalibrationResponse(
        code=0,
        project_state=app.state.projects[project_id]["project_state"],
        calibration_pid=app.state.projects[project_id]["vggt_calibration_pid"],
        message="VGGT calibration started successfully"
    )


async def monitor_postprocess_completion(project_id: str, process: subprocess.Popen):
    """Monitor the shared layout post-processing subprocess."""
    return_code = await asyncio.get_event_loop().run_in_executor(None, process.wait)
    project = app.state.projects[project_id]
    log_file = project.get("postprocess_log")
    if log_file is not None:
        log_file.close()
        project["postprocess_log"] = None
    project["postprocess_pid"] = None
    app.state.running_calibration = False

    if return_code == 0:
        project["postprocess_state"] = "COMPLETED"
    else:
        project["postprocess_state"] = "ERROR"
    # Raw calibration results remain valid even when derived outputs fail.
    refresh_project_state(project)


@app.post(f"{API_VERSION_PREFIX}/postprocess/{{project_id}}", tags=["Calibration"])
async def run_layout_postprocess(
    project_id: str, background_tasks: BackgroundTasks
) -> CalibrationResponse:
    """Generate derived artifacts for every available calibration result tree."""
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    if app.state.running_calibration:
        raise HTTPException(
            status_code=400,
            detail="Calibration or post-processing is already running.",
        )

    project = app.state.projects[project_id]
    reconcile_alignment_artifacts(project_id)
    missing_artifacts = missing_postprocess_artifacts(project)
    if missing_artifacts:
        raise HTTPException(
            status_code=400,
            detail=(
                "Post-processing requires layout and alignment artifacts. "
                f"Missing or empty: {', '.join(missing_artifacts)}. "
                "Re-upload the layout and save the alignment again."
            ),
        )
    if not check_postprocess_ready(project):
        raise HTTPException(
            status_code=400,
            detail=(
                "Post-processing requires a completed multi-camera calibration "
                "and layout alignment artifacts."
            ),
        )
    try:
        await _sync_cam_dir_for_project(project)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update config file: {e}")

    project_dir = project["project_dir"]
    command = "bash launch_LayoutPostProcess.sh"
    command += f" -a ../server/{project_dir}/output"
    command += f" -l ../server/{project_dir}/output/alignment_data/layout.png"
    if project["groundtruth_file"]:
        command += f" -g ../server/{project_dir}/groundtruth"
    command += " -m local --no-plot"

    log_file_path = Path(project_dir) / "postprocess.log"
    log_file = open(log_file_path, "w")
    log_file.write(f"Running command: {command}\n")
    log_file.flush()

    project["postprocess_log"] = log_file
    project["postprocess_state"] = "RUNNING"
    project["project_state"] = "RUNNING"
    app.state.running_calibration = True
    try:
        process = subprocess.Popen(
            ["bash", "-c", command],
            cwd="../service_scripts/",
            text=True,
            bufsize=1,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
    except Exception as exc:
        log_file.close()
        project["postprocess_log"] = None
        project["postprocess_state"] = "ERROR"
        project["project_state"] = "COMPLETED"
        app.state.running_calibration = False
        raise HTTPException(
            status_code=500, detail=f"Failed to start post-processing: {exc}"
        )

    project["postprocess_pid"] = process.pid
    background_tasks.add_task(monitor_postprocess_completion, project_id, process)
    return CalibrationResponse(
        code=0,
        project_state=project["project_state"],
        calibration_pid=process.pid,
        message="Post-processing started successfully",
    )


# Stop Calibration Process
@app.post(f"{API_VERSION_PREFIX}/stop_calibration/{{project_id}}", tags=["Calibration"])
async def stop_calibration(project_id: str) -> StandardResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    project = app.state.projects[project_id]
    active_jobs = (
        ("amc_state", "calibration_pid", "calibration_log"),
        ("vggt_state", "vggt_calibration_pid", "vggt_calibration_log"),
        ("postprocess_state", "postprocess_pid", "postprocess_log"),
    )
    active = next((job for job in active_jobs if project.get(job[0]) == "RUNNING"), None)
    if active is None:
        raise HTTPException(status_code = 400, detail = "Calibration is not running.")
        # return {"message": "Calibration is not running."}
    state_key, pid_key, log_key = active
    pid = project.get(pid_key)
    if pid is None:
        raise HTTPException(status_code=500, detail="Running job has no process id.")
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to stop calibration: {e}")
    log_file = project.get(log_key)
    if log_file is not None:
        log_file.close()
        project[log_key] = None
    project[pid_key] = None
    project[state_key] = "ERROR"
    app.state.running_calibration = False
    refresh_project_state(project)
    return StandardResponse(code=0, message="Calibration stopped successfully")


# Relaunch the calibration
@app.post(f"{API_VERSION_PREFIX}/relaunch_calibration/{{project_id}}", tags=["Calibration"])
async def relaunch_calibration(project_id: str) -> StandardResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Relaunch is not allowed. First stop the calibration and then relaunch it.")
    _delete_result(project_id)
    _verify_project(project_id)
    return StandardResponse(
        code=0,
        project_state=app.state.projects[project_id]["project_state"],
        message="Calibration is ready to be relaunched successfully"
    )


# Get the calibration log
@app.get(f"{API_VERSION_PREFIX}/{{type}}/calibrate/{{project_id}}/log", tags=["Calibration"])
async def get_calibration_log(project_id: str, type: str) -> FileResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"meossage": "Project not found"}
    if app.state.projects[project_id]["project_state"] == "INIT" or app.state.projects[project_id]["project_state"] == "READY":
        raise HTTPException(status_code = 400, detail = "Calibration has not run yet. Please run the calibration first.")
        # return {"message": "Calibration has not run yet. Please run the calibration first."}
    if type == "amc":
        if app.state.projects[project_id]["amc_state"] == "INIT" or app.state.projects[project_id]["amc_state"] == "READY":
            raise HTTPException(status_code=400, detail="AMC calibration has not run yet. Please run the calibration first.")
        log_file_path = Path(app.state.projects[project_id]["project_dir"]) / Path("calibration.log")
    elif type == "vggt":
        if app.state.projects[project_id]["vggt_state"] == "INIT" or app.state.projects[project_id]["vggt_state"] == "READY":
            raise HTTPException(status_code=400, detail="VGGT calibration has not run yet. Please run the calibration first.")
        log_file_path = Path(app.state.projects[project_id]["project_dir"]) / Path("vggt_calibration.log")
    else:
        raise HTTPException(status_code=400, detail="Invalid type")
    if log_file_path.exists():
        return FileResponse(log_file_path, media_type="text/plain", filename="calibration.log")
    else:
        raise HTTPException(status_code = 400, detail = "Calibration log not found")
        # return {"message": "Calibration log not found"}


# Stream live calibration logs
@app.get(f"{API_VERSION_PREFIX}/calibrate/{{project_id}}/log/{{type}}/stream", tags=["Calibration"])
async def stream_calibration_log(project_id: str, type: str):
    """Stream calibration logs in real-time using Server-Sent Events (SSE)."""
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    project_state = app.state.projects[project_id]["project_state"]
    if project_state == "INIT" or project_state == "READY":
        raise HTTPException(status_code=400, detail="Calibration has not run yet. Please run the calibration first.")
    
    if type == "amc":
        if app.state.projects[project_id]["amc_state"] == "INIT" or app.state.projects[project_id]["amc_state"] == "READY":
            raise HTTPException(status_code=400, detail="AMC calibration has not run yet. Please run the calibration first.")
        log_file_path = Path(app.state.projects[project_id]["project_dir"]) / Path("calibration.log")
    elif type == "vggt":    
        if app.state.projects[project_id]["vggt_state"] == "INIT" or app.state.projects[project_id]["vggt_state"] == "READY":
            raise HTTPException(status_code=400, detail="VGGT calibration has not run yet. Please run the calibration first.")
        log_file_path = Path(app.state.projects[project_id]["project_dir"]) / Path("vggt_calibration.log")
    else:
        raise HTTPException(status_code=400, detail="Invalid type")
    
    if not log_file_path.exists():
        raise HTTPException(status_code=400, detail="Calibration log not found")
    
    async def log_generator():
        """Generator that yields new log lines as they are written."""
        last_position = 0
        while True:
            try:
                with open(log_file_path, "r") as f:
                    f.seek(last_position)
                    new_content = f.read()
                    if new_content:
                        last_position = f.tell()
                        # Send as SSE format
                        for line in new_content.splitlines(keepends=True):
                            yield f"data: {line}\n"
                        yield "\n"
                
                # Check if calibration is still running
                current_state = app.state.projects.get(project_id, {}).get("project_state", "")
                if current_state in ["COMPLETED", "ERROR"]:
                    # Send final message and close
                    yield f"data: [CALIBRATION {current_state}]\n\n"
                    break
                
                # Wait before checking for new content
                await asyncio.sleep(0.5)
            except Exception as e:
                yield f"data: [ERROR: {str(e)}]\n\n"
                break
    
    return StreamingResponse(
        log_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# Get the calibration result
@app.get(f"{API_VERSION_PREFIX}/result/{{project_id}}/overlay_image", tags=["Calibration"])
async def get_result(project_id: str) -> FileResponse:
    project = _require_completed_pipeline(project_id, "amc")
    if project['video_files_count'] == 1:
        output_dir = Path(project["project_dir"]) / Path("output/single_view_results/cam_00/")
        image_name = "verification_map_overlay.png"
    else:
        output_dir = Path(project["project_dir"]) / Path("output/multi_view_results/BA_output/results_ba_scaled_world/")
        image_name = f"overlay_img_{project['video_files_count'] - 2:02d}.png" if project['video_files_count'] > 1 else "overlay_img_00.png"
    if (output_dir / image_name).exists():
        return FileResponse(output_dir / image_name, media_type="image/png")
    raise HTTPException(status_code = 400, detail = f"Overlay image {image_name} not found")


# Get the VGGT results overlay image
@app.get(f"{API_VERSION_PREFIX}/vggt_results/{{project_id}}/overlay_image", tags=["Calibration"])
async def get_vggt_result(project_id: str) -> FileResponse:
    project = _require_completed_pipeline(project_id, "vggt")
    output_dir = Path(project["project_dir"]) / Path("output/vggt_results/multi_view_results/BA_output/results_ba_scaled_world/")

    num_cams = project["video_files_count"]
    image_name = f"overlay_img_{num_cams - 2:02d}.png"
    if (output_dir / image_name).exists():
        return FileResponse(output_dir / image_name, media_type="image/png")
    raise HTTPException(status_code = 400, detail = f"VGGT overlay image {image_name} not found")


@app.get(
    f"{API_VERSION_PREFIX}/result/{{project_id}}/virtual_gt_overlay/{{camera_id}}",
    tags=["Calibration"],
)
async def get_virtual_gt_overlay(
    project_id: str, camera_id: int, result_type: str = "amc"
) -> FileResponse:
    """Serve per-camera virtual-GT overlays for a completed AMC or VGGT run."""
    if result_type not in ("amc", "vggt"):
        raise HTTPException(status_code=400, detail="result_type must be amc or vggt")
    project = _require_completed_pipeline(project_id, result_type)

    project_dir = Path(project["project_dir"])
    result_root = project_dir / "output"
    if result_type == "vggt":
        result_root /= "vggt_results"
    image_path = result_root / "virtual_gt_overlays" / f"cam_{camera_id:02d}.png"
    if not image_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                "Virtual-GT overlay is not available; the visualizer integration "
                "is not installed."
            ),
        )
    return FileResponse(image_path, media_type="image/png")


# Get the camera parameters
@app.get(f"{API_VERSION_PREFIX}/result/{{project_id}}/camera_parameters/{{camera_id}}", tags=["Calibration"])
async def get_camera_parameters(project_id: str, camera_id: int) -> FileResponse:
    project = _require_completed_pipeline(project_id, "amc")
    if project["video_files_count"] == 1:
        output_dir = Path(project["project_dir"]) / Path("output/single_view_results/cam_00/")
        cam_info_path = output_dir / f"camInfo_world.yaml"
    else:
        output_dir = Path(project["project_dir"]) / Path("output/multi_view_results/BA_output/results_ba_scaled_world/")
        cam_info_path = output_dir / f"camInfo_{camera_id:02d}.yaml" if project["video_files_count"] > 1 else "camInfo_hyper_00.yaml"
    if cam_info_path.exists():
        return FileResponse(cam_info_path, media_type="text/yaml")
    raise HTTPException(
        status_code=400,
        detail=(
            f"World-scaled AMC camera parameters for camera id {camera_id} not found "
            f"({cam_info_path.as_posix()}). Run post-processing first."
        ),
    )


# Get the VGGT results camera parameters
@app.get(f"{API_VERSION_PREFIX}/vggt_results/{{project_id}}/camera_parameters/{{camera_id}}", tags=["Calibration"])
async def get_vggt_camera_parameters(project_id: str, camera_id: int) -> FileResponse:
    project = _require_completed_pipeline(project_id, "vggt")
    output_dir = Path(project["project_dir"]) / Path("output/vggt_results/multi_view_results/BA_output/results_ba_scaled_world/")
    cam_info_path = output_dir / f"camInfo_{camera_id:02d}.yaml"
    if cam_info_path.exists():
        return FileResponse(cam_info_path, media_type="text/yaml")
    raise HTTPException(
        status_code=400,
        detail=(
            f"World-scaled VGGT camera parameters for camera id {camera_id} not found "
            f"({cam_info_path.as_posix()}). Run post-processing first."
        ),
    )


# Get the calibration result zip file
@app.get(f"{API_VERSION_PREFIX}/result/{{project_id}}", tags=["Calibration"])
async def get_result_zip(project_id: str) -> FileResponse:
    project = _require_any_completed_calibration(project_id)
    dir_to_archive = Path(project["project_dir"]) / Path("output")
    archive_name = "output.zip"
    archive_path = Path(project["project_dir"]) / Path(archive_name)
    if dir_to_archive.exists():
        try:
            shutil.make_archive(archive_path.with_suffix(""), "zip", dir_to_archive)
        except Exception as e:
            raise HTTPException(status_code = 500, detail = f"Failed to create {archive_name} file: {e}")
        return FileResponse(archive_path, media_type="application/zip")
    raise HTTPException(status_code = 400, detail = "Calibration result directory not found")


# Get AMC evaluation metrics (zip file)
@app.get(f"{API_VERSION_PREFIX}/result/{{project_id}}/evaluation_metrics", tags=["Calibration"])
async def get_evaluation_metrics(project_id: str) -> FileResponse:
    project = _require_completed_pipeline(project_id, "amc")
    if not project["groundtruth_file"]:
        raise HTTPException(status_code = 400, detail = "Groundtruth file is required for evaluation.")
    project_dir = Path(project["project_dir"])
    video_count = project.get("video_files_count", 0)
    if video_count == 1:
        dir_to_archive = project_dir / "output" / "single_view_results" / "evaluation"
    else:
        dir_to_archive = project_dir / "output" / "multi_view_results" / "evaluation"
    archive_name = "evaluation.zip"
    archive_path = Path(project["project_dir"]) / Path(archive_name)
    if dir_to_archive.exists():
        try:
            shutil.make_archive(archive_path.with_suffix(""), "zip", dir_to_archive)
        except Exception as e:
            raise HTTPException(status_code = 500, detail = f"Failed to create {archive_name} file: {e}")
        return FileResponse(archive_path, media_type="application/zip")
    raise HTTPException(status_code = 400, detail = "Evaluation metrics not found")


# Get VGGT evaluation metrics (zip file)
@app.get(f"{API_VERSION_PREFIX}/vggt_results/{{project_id}}/evaluation_metrics", tags=["Calibration"])
async def get_vggt_evaluation_metrics(project_id: str) -> FileResponse:
    project = _require_completed_pipeline(project_id, "vggt")
    if not project["groundtruth_file"]:
        raise HTTPException(status_code = 400, detail = "Groundtruth file is required for evaluation.")
    dir_to_archive = Path(project["project_dir"]) / Path("output/vggt_results/multi_view_results/evaluation/")
    archive_name = "vggt_evaluation.zip"
    archive_path = Path(project["project_dir"]) / Path(archive_name)
    if dir_to_archive.exists():
        try:
            shutil.make_archive(archive_path.with_suffix(""), "zip", dir_to_archive)
        except Exception as e:
            raise HTTPException(status_code = 500, detail = f"Failed to create {archive_name} file: {e}")
        return FileResponse(archive_path, media_type="application/zip", filename=archive_name)
    raise HTTPException(status_code = 400, detail = "VGGT evaluation metrics not found")


# Get evaluation metrics
@app.get(f"{API_VERSION_PREFIX}/result/{{project_id}}/evaluation_layout", tags=["Calibration"])
async def get_evaluation_layout(project_id: str) -> FileResponse:
    project = _require_completed_pipeline(project_id, "amc")
    project_dir = Path(project["project_dir"])
    video_count = project.get("video_files_count", 0)
    if video_count == 1:
        image_path = project_dir / "output" / "single_view_results" / "evaluation" / "results_layout_3d_points.png"
    else:
        image_path = project_dir / "output" / "multi_view_results" / "evaluation" / "results_layout_3d_points.png"
    if not image_path.exists():
            raise HTTPException(status_code = 400, detail = "Evaluation image not found")
    return FileResponse(image_path, media_type="image/png")


# Get VGGT evaluation layout
@app.get(f"{API_VERSION_PREFIX}/vggt_results/{{project_id}}/evaluation_layout", tags=["Calibration"])
async def get_vggt_evaluation_layout(project_id: str) -> FileResponse:
    project = _require_completed_pipeline(project_id, "vggt")
    output_dir = Path(project["project_dir"]) / Path("output/vggt_results/multi_view_results/evaluation/")
    image_path = output_dir / "results_layout_3d_points.png"
    if image_path.exists():
        return FileResponse(image_path, media_type="image/png")
    raise HTTPException(status_code = 400, detail = "VGGT evaluation image not found")


# Get evaluation statistics
@app.get(f"{API_VERSION_PREFIX}/result/{{project_id}}/evaluation_statistics", tags=["Calibration"])
async def get_evaluation_statistics(project_id: str) -> EvaluationStatisticsResponse:
    project = _require_completed_pipeline(project_id, "amc")

    project_dir = Path(project["project_dir"])
    video_count = project.get("video_files_count", 0)
    # Single-cam: evaluation is under single_view_results/evaluation; multi-cam: multi_view_results/evaluation
    if video_count == 1:
        stats_path = project_dir / "output" / "single_view_results" / "evaluation" / "statistics.txt"
    else:
        stats_path = project_dir / "output" / "multi_view_results" / "evaluation" / "statistics.txt"
    if not stats_path.exists():
        raise HTTPException(status_code = 400, detail = "Evaluation statistics not found")

    try:
        # Read and parse statistics file
        with open(stats_path, 'r') as f:
            content = f.read()

        # Parse the statistics
        stats = {}
        for line in content.strip().split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                stats[key.strip()] = value.strip()

        return EvaluationStatisticsResponse(
            code=0,
            message="Evaluation statistics retrieved successfully",
            statistics=stats,
            raw_text=content
        )
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to read evaluation statistics: {e}")


# Get VGGT evaluation statistics
@app.get(f"{API_VERSION_PREFIX}/vggt_results/{{project_id}}/evaluation_statistics", tags=["Calibration"])
async def get_vggt_evaluation_statistics(project_id: str) -> EvaluationStatisticsResponse:
    project = _require_completed_pipeline(project_id, "vggt")

    output_dir = Path(project["project_dir"]) / Path("output/vggt_results/multi_view_results/evaluation/")
    stats_path = output_dir / "statistics.txt"

    if not stats_path.exists():
        raise HTTPException(status_code = 400, detail = "VGGT evaluation statistics not found")

    try:
        # Read and parse statistics file
        with open(stats_path, 'r') as f:
            content = f.read()

        # Parse the statistics
        stats = {}
        for line in content.strip().split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                stats[key.strip()] = value.strip()

        return EvaluationStatisticsResponse(
            code=0,
            message="VGGT evaluation statistics retrieved successfully",
            statistics=stats,
            raw_text=content
        )
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to read VGGT evaluation statistics: {e}")


# Get the MV3DT result
@app.get(f"{API_VERSION_PREFIX}/result/{{project_id}}/mv3dt_result", tags=["Calibration"])
async def get_mv3dt_result(project_id: str, result_type: str = "amc") -> FileResponse:
    if result_type not in ("amc", "vggt"):
        raise HTTPException(status_code=400, detail="result_type must be amc or vggt")
    project = _require_completed_pipeline(project_id, result_type)
    if result_type == "amc" and project["video_files_count"] == 1:
        raise HTTPException(status_code = 400, detail = "MV3DT result is not generated for single-view calibration.")
    if not check_export_ready(
        project["project_dir"], project["video_files_count"], result_type
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{result_type.upper()} export artifacts are not available. "
                "Run post-processing after calibration completes."
            ),
        )

    # Determine paths based on result type
    project_dir = Path(project["project_dir"])
    if result_type == "vggt":
        dir_to_archive = project_dir / "output" / "vggt_results" / "mv3dt_output"
        output_dir = project_dir / "output" / "vggt_results"
        archive_name = "vggt_mv3dt_output.zip"
    else:
        dir_to_archive = project_dir / "output" / "mv3dt_output"
        output_dir = project_dir / "output"
        archive_name = "mv3dt_output.zip"

    archive_path = output_dir / archive_name
    mv3dt_result_path = dir_to_archive / "transforms.yml"

    if mv3dt_result_path.exists():
        try:
            shutil.make_archive(archive_path.with_suffix(""), "zip", dir_to_archive)
        except Exception as e:
            raise HTTPException(status_code = 500, detail = f"Failed to create {archive_name} file: {e}")
        return FileResponse(archive_path, media_type="application/zip", filename=archive_name)
    raise HTTPException(status_code = 400, detail = f"{result_type.upper()} MV3DT results not found")


# Delete the calibration result
def _delete_result(project_id: str):
    if project_id not in app.state.projects:
        raise HTTPException(status_code = 404, detail = "Project not found")
        # return {"message": "Project not found"}
    project_dir = Path(app.state.projects[project_id]["project_dir"])
    output_dir = project_dir / "output"
    alignment_data_dir = output_dir / "alignment_data"
    
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code = 400, detail = "Calibration is running. Deletion is not allowed.")
        # return {"message": "Calibration is running. Deletion is not allowed."}
    if app.state.projects[project_id].get("postprocess_state") == "RUNNING":
        raise HTTPException(
            status_code=400,
            detail="Post-processing is running. Deletion is not allowed.",
        )
    
    # Backup alignment_data files before deletion.
    # Single-camera uploads store points directly as alignment_data.json (no _orig seed to
    # regenerate from), so it must survive. Multi-camera keeps only alignment_data_orig.json:
    # its alignment_data.json is derived in rectified space and must be regenerated per run.
    preserved_filenames = ["layout.png", "alignment_data_orig.json", "annotations_orig.json"]
    has_orig_seed = _is_non_empty_file(alignment_data_dir / "alignment_data_orig.json")
    if app.state.projects[project_id]["video_files_count"] == 1 or not has_orig_seed:
        # Deleting results must not delete alignment input: a rectified-space
        # save has no seed to regenerate from, and the staged media it matches
        # survives this reset.
        preserved_filenames.append("alignment_data.json")

    files_to_preserve = {}
    if alignment_data_dir.exists():
        for filename in preserved_filenames:
            file_path = alignment_data_dir / filename
            if file_path.exists():
                try:
                    with open(file_path, 'rb') as f:
                        files_to_preserve[filename] = f.read()
                except Exception as e:
                    logger.warning(f"Failed to backup {filename}: {e}")
    
    # Preserve upstream linear-media artifacts. They are inputs to E2E now,
    # not disposable calibration results.
    linear_backup = project_dir / ".linear_media_backup"
    if linear_backup.exists():
        shutil.rmtree(linear_backup)
    sv_results = output_dir / "single_view_results"
    upstream_names = {
        "rectified.mp4",
        "rectified.jpg",
        "background.jpg",
        "distortion.yaml",
        "geocalib_distortion.yaml",
        "distortion_estimation",
    }
    if sv_results.exists():
        for camera_dir in sv_results.iterdir():
            if not camera_dir.is_dir():
                continue
            for name in upstream_names:
                source = camera_dir / name
                if source.exists() or source.is_symlink():
                    destination = linear_backup / camera_dir.name / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(source.as_posix(), destination.as_posix())

    # Delete calibration outputs.
    try:
        if output_dir.exists():
            shutil.rmtree(output_dir)
    except Exception as e:
        raise HTTPException(status_code = 500, detail = f"Failed to delete calibration result: {e}")
        # return {"message": f"Failed to delete calibration result: {e}"}

    if linear_backup.exists():
        restored_sv = output_dir / "single_view_results"
        restored_sv.mkdir(parents=True, exist_ok=True)
        for camera_dir in linear_backup.iterdir():
            shutil.move(camera_dir.as_posix(), (restored_sv / camera_dir.name).as_posix())
        linear_backup.rmdir()

    _delete_exported_calibration_artifacts(project_dir)

    # Recreate alignment_data folder and restore preserved files
    if files_to_preserve:
        try:
            alignment_data_dir.mkdir(parents=True, exist_ok=True)
            for filename, content in files_to_preserve.items():
                with open(alignment_data_dir / filename, 'wb') as f:
                    f.write(content)
        except Exception as e:
            logger.warning(f"Failed to restore alignment_data files: {e}")
    # Results are gone, but the verified source media and staged linear
    # artifacts remain. Re-derive both pipeline states from those shared
    # prerequisites instead of privileging AMC through project_state.
    project = app.state.projects[project_id]
    project["amc_state"] = "INIT"
    project["vggt_state"] = "INIT"
    project["amc_results_available"] = False
    project["postprocess_state"] = "INIT"
    project["postprocess_pid"] = None
    project["postprocess_log"] = None
    reconcile_alignment_artifacts(project_id)
    enable_amc_if_eligible(project)
    enable_vggt_if_eligible(project)
    refresh_project_state(project)


@app.delete(f"{API_VERSION_PREFIX}/result/{{project_id}}", tags=["Calibration"])
async def delete_result(project_id: str) -> StandardResponse:
    _delete_result(project_id)
    return StandardResponse(
        code=0,
        message="Calibration result deleted successfully"
    )


# Export calibration with ROI/Tripwire world coordinates
@app.post(f"{API_VERSION_PREFIX}/result/{{project_id}}/export_calibration", tags=["Calibration"])
async def export_calibration(
    project_id: str,
    result_type: str = "amc",
    calibration_type: str = "cartesian",
) -> ExportCalibrationResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")

    if calibration_type not in ("cartesian", "image"):
        raise HTTPException(
            status_code=400,
            detail="calibration_type must be 'cartesian' or 'image'",
        )

    project_dir = Path(app.state.projects[project_id]["project_dir"])
    camera_name_map = app.state.projects[project_id].get("camera_name_map", {})
    video_count = app.state.projects[project_id].get("video_files_count", 0)

    try:
        if calibration_type == "image":
            success, message, output_path = export_image_calibration_for_project(
                project_dir,
                camera_name_map=camera_name_map,
                project_id=project_id,
            )
        else:
            # Validate based on result type (cartesian export requires finished calibration)
            if result_type == "vggt":
                _require_completed_pipeline(project_id, "vggt")
            else:
                _require_completed_pipeline(project_id, "amc")
            if not check_export_ready(project_dir.as_posix(), video_count, result_type):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"World-scaled {result_type.upper()} camera parameters are not "
                        "available. Run post-processing after calibration completes."
                    ),
                )

            success, message, output_path = export_calibration_for_project(
                project_dir,
                result_type=result_type,
                camera_name_map=camera_name_map,
                is_single_camera=(video_count == 1),
            )

        if success:
            return ExportCalibrationResponse(
                code=0,
                message=message,
                export_file=output_path.name if output_path else None,
            )
        _raise_export_failure(message, "Failed to export calibration")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to export calibration: {e}")


# Save edited calibration JSON as project export file (so future Full Export loads it)
@app.put(f"{API_VERSION_PREFIX}/result/{{project_id}}/export_calibration", tags=["Calibration"])
async def save_exported_calibration(
    request: Request,
    project_id: str,
    result_type: str = "amc",
    calibration_type: str = "cartesian",
) -> ExportCalibrationResponse:
    """Save calibration JSON to project's export file. Accepts raw JSON text to preserve formatting (e.g. 0.0)."""
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    if calibration_type not in ("cartesian", "image"):
        raise HTTPException(
            status_code=400,
            detail="calibration_type must be 'cartesian' or 'image'",
        )
    raw_body = await request.body()
    try:
        raw_text = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Request body must be valid UTF-8")
    raw_text = raw_text.strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="Request body must be non-empty JSON")
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    # Cartesian *_exported.json: drop per-sensor projected globals (kept only in *_projected.json).
    if calibration_type == "cartesian":
        sensors = parsed.get("sensors")
        if isinstance(sensors, list):
            for s in sensors:
                if isinstance(s, dict):
                    s.pop("projectedGlobalRois", None)
                    s.pop("projectedGlobalTripwires", None)
        raw_text = json.dumps(parsed, indent=2)
    project_dir = Path(app.state.projects[project_id]["project_dir"])
    project_name = project_dir.name
    if calibration_type == "image":
        stored_project_id = project_name.removeprefix("project_")
        export_file = project_dir / f"{stored_project_id}_image_mode_exported.json"
    else:
        suffix = f"_{result_type}" if result_type == "vggt" else ""
        export_file = project_dir / f"{project_name}_exported{suffix}.json"
    try:
        with open(export_file, "w", encoding="utf-8") as f:
            f.write(raw_text)
        return ExportCalibrationResponse(
            code=0,
            message=f"Saved {result_type.upper()} calibration to {export_file.name}",
            export_file=export_file.name,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save calibration: {e}")


# Download exported calibration JSON
@app.get(f"{API_VERSION_PREFIX}/result/{{project_id}}/export_calibration", tags=["Calibration"])
async def download_exported_calibration(
    project_id: str,
    result_type: str = "amc",
    calibration_type: str = "cartesian",
    snapshot: str = "exported",
) -> FileResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    if calibration_type not in ("cartesian", "image"):
        raise HTTPException(
            status_code=400,
            detail="calibration_type must be 'cartesian' or 'image'",
        )
    if snapshot not in ("exported", "projected"):
        raise HTTPException(
            status_code=400,
            detail="snapshot must be 'exported' or 'projected'",
        )

    project_dir = Path(app.state.projects[project_id]["project_dir"])
    project_name = project_dir.name

    if calibration_type == "image":
        stored_project_id = project_name.removeprefix("project_")
        download_name = f"{stored_project_id}_image_mode_exported.json"
        export_file = project_dir / download_name
    else:
        suffix = f"_{result_type}" if result_type == "vggt" else ""
        snapshot_stem = "projected" if snapshot == "projected" else "exported"
        download_name = f"{project_name}_{snapshot_stem}{suffix}.json"
        export_file = project_dir / download_name

    if not export_file.exists():
        raise HTTPException(
            status_code=404,
            detail="Export file not found. Please export calibration first.",
        )

    return FileResponse(export_file, media_type="application/json", filename=download_name)


# Generate ROI/Tripwire BEV visualization
@app.post(f"{API_VERSION_PREFIX}/result/{{project_id}}/generate_roi_bev", tags=["Calibration"])
async def generate_roi_bev_visualization(project_id: str, result_type: str = "amc") -> Response:
    """
    Writes ``{project}_projected[_vggt].json`` (same schema as full export) then generates BEV from it.
    Does not require a prior Full Export.
    """
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    
    try:
        project_dir = Path(app.state.projects[project_id]["project_dir"])
        project_name = project_dir.name
        camera_name_map = app.state.projects[project_id].get("camera_name_map", {})
        video_count = app.state.projects[project_id].get("video_files_count", 0)

        if result_type == "vggt":
            _require_completed_pipeline(project_id, "vggt")
        else:
            _require_completed_pipeline(project_id, "amc")

        success, msg, out_path = export_calibration_for_project(
            project_dir,
            result_type=result_type,
            camera_name_map=camera_name_map,
            is_single_camera=(video_count == 1),
            output_stem="projected",
        )
        if not success or out_path is None:
            _raise_export_failure(msg, "Failed to build verification snapshot")

        export_file = out_path
        layout_file = project_dir / "manual_adjustment" / "layout.png"
        layout_path = layout_file if layout_file.exists() else None

        # Generate BEV visualization using existing utility
        # Pass project_dir so it can load transform directly from transform_world_to_map.json
        fig_with_layout, fig_coords_only = generate_bev_verification(
            export_file, layout_path, project_path=project_dir, result_type=result_type
        )

        fig_out = fig_with_layout or fig_coords_only
        if not fig_out:
            raise HTTPException(status_code=500, detail="Failed to generate BEV visualization")

        # Convert figure to image bytes
        import io
        buf = io.BytesIO()
        fig_out.savefig(buf, format='png', dpi=100, bbox_inches='tight')
        import matplotlib.pyplot as plt
        if fig_with_layout:
            plt.close(fig_with_layout)
        if fig_coords_only:
            plt.close(fig_coords_only)
        buf.seek(0)
        
        return Response(content=buf.read(), media_type="image/png")
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to generate ROI BEV: {str(e)}")


# Check if export file exists
@app.get(f"{API_VERSION_PREFIX}/result/{{project_id}}/export_exists", tags=["Calibration"])
async def check_export_exists(
    project_id: str, calibration_type: str = "cartesian"
) -> ExportExistsResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    if calibration_type not in ("cartesian", "image"):
        raise HTTPException(
            status_code=400,
            detail="calibration_type must be 'cartesian' or 'image'",
        )

    project_dir = Path(app.state.projects[project_id]["project_dir"])
    project_name = project_dir.name
    if calibration_type == "image":
        stored_project_id = project_name.removeprefix("project_")
        export_file = project_dir / f"{stored_project_id}_image_mode_exported.json"
        fname = f"{stored_project_id}_image_mode_exported.json"
    else:
        export_file = project_dir / f"{project_name}_exported.json"
        fname = f"{project_name}_exported.json"

    return ExportExistsResponse(
        code=0,
        exists=export_file.exists(),
        export_file=fname if export_file.exists() else None,
        message="Export file exists" if export_file.exists() else "Export file not found",
    )

#------- RTSP Stream Capture API (via VIOS) ------------------------------------------------------#

# ---------------------------------------------------------------------------
# Background task: resolve sensors → record → ingest into project
# ---------------------------------------------------------------------------

def _validate_rtsp_camera_name_matches_vios(
    requested_name: str,
    vios_sensor_name: Optional[str],
    sensor_id: str,
) -> None:
    if not vios_sensor_name or vios_sensor_name == requested_name:
        return
    raise ViosClientError(
        "Camera name clash for an existing VIOS sensor: "
        f"user input camera_name '{requested_name}' does not match VIOS stored "
        f"camera name '{vios_sensor_name}' for sensor_id '{sensor_id}'. "
        f"Please input camera_name '{vios_sensor_name}' and start RTSP capture again."
    )


async def _run_rtsp_capture(session_id: str) -> None:
    """
    Background asyncio task that drives the RTSP capture lifecycle for VIOS.

    VIOS operates in ``alwaysRecording`` mode – recording starts automatically
    when a sensor is registered; there is no per-session start/stop API.
    For sensors already registered (``sensor_id`` provided in RtspStreamConfig and exists in VIOS)
    no registration call is made.

    Workflow:
      1. For each stream, resolve the VIOS sensor_id:
           a. If ``sensor_id`` is set and exists in VIOS → use it.
           b. If ``sensor_id`` is set but not found (404) → register with ``rtsp_url``.
           c. If ``sensor_id`` is omitted → register with ``rtsp_url``.
      2. Verify each sensor is recording (``alwaysOn`` status).
      3. Record ``recording_start_time = now()``.
      4. Sleep for ``duration_seconds`` (cancellable).
      5. Record ``recording_end_time = now()``, stop AMC-owned recordings,
         then ingest clips into the project.

    Session status transitions:
        STARTING → RECORDING → STOPPING → INGESTING → INGESTED
        STARTING → CANCELLED (via stop endpoint before recording starts)
        Any stage → ERROR (on failure)
    """
    sess = app.state.rtsp_sessions.get(session_id)
    if sess is None:
        return

    vios = ViosClient(
        base_url=sess["vios_base_url"],
        token=sess.get("vios_token"),
        ssl_verify=sess.get("ssl_verify", False),
    )

    resolved_sensor_ids: list = []
    # Persisted in the session dict so they survive for debugging even if the task frame is gone.
    sess["sensors_started_by_us"] = []
    # Sensors AMC MS created (registered) in VIOS on behalf of this session.
    # On session deletion these are deregistered; pre-existing reused sensors
    # are NOT in this list and must remain in VIOS.
    sess["sensors_registered_by_us"] = []

    async def _stop_our_recordings() -> list:
        """Stop recording for sensors AMC MS started.  Returns list of sensor IDs that failed."""
        sids = sess["sensors_started_by_us"]
        if not sids:
            return []
        results = await asyncio.gather(*[vios.stop_recording(sid) for sid in sids], return_exceptions=True)
        failed = []
        for sid, result in zip(sids, results):
            if result is True:
                logger.info("RTSP session %s: stopped recording for sensor %s.", session_id, sid)
            else:
                logger.warning(
                    "RTSP session %s: could not stop recording for sensor %s.", session_id, sid
                )
                failed.append(sid)
        return failed

    try:
        # ------------------------------------------------------------------ #
        # Step 1 – Resolve sensor IDs and ensure recording is starting.
        #          For each stream:
        #            - Pre-registered sensor (verified): start_recording() if
        #              it is not already recording. The requested camera_name
        #              must match VIOS's stored sensor name to avoid ingest and
        #              export label ambiguity.
        #            - Otherwise: register the sensor. VIOS auto-starts
        #              recording on registration (alwaysRecording), so no
        #              explicit start call is needed — and querying status
        #              right after registration returns stale data.
        #          Newly-registered sensors are added to sensors_started_by_us
        #          because VIOS is recording them on our behalf. Pre-existing
        #          sensors, including duplicate-URL reuse from register_sensor,
        #          are added only when our start_recording() succeeds.
        # ------------------------------------------------------------------ #
        sess["status"] = "STARTING"
        streams = sess["streams"]

        async def _start_preexisting_sensor_if_needed(sensor_id: str) -> None:
            if await vios.is_sensor_recording(sensor_id):
                return
            started = await vios.start_recording(sensor_id)
            if started:
                if sensor_id not in sess["sensors_started_by_us"]:
                    sess["sensors_started_by_us"].append(sensor_id)
                logger.info(
                    "RTSP session %s: start_recording succeeded for sensor %s.",
                    session_id, sensor_id,
                )
            else:
                logger.warning(
                    "RTSP session %s: start_recording failed for sensor %s — "
                    "verify loop will determine actual state.",
                    session_id, sensor_id,
                )

        for idx, stream_cfg in enumerate(streams):
            camera_name = stream_cfg.get("camera_name") or f"cam_{idx:02d}"
            raw_sid = stream_cfg.get("sensor_id")
            preset_sid = (raw_sid.strip() if isinstance(raw_sid, str) else None) or None
            rtsp_url = stream_cfg["rtsp_url"]

            if preset_sid and await vios.verify_sensor_registered(preset_sid):
                registered_camera_name = await vios.get_sensor_name(preset_sid)
                _validate_rtsp_camera_name_matches_vios(
                    requested_name=camera_name,
                    vios_sensor_name=registered_camera_name,
                    sensor_id=preset_sid,
                )
                logger.info(
                    "RTSP session %s: using pre-registered sensor '%s' (id=%s)",
                    session_id, camera_name, preset_sid,
                )
                resolved_sensor_ids.append(preset_sid)
                await _start_preexisting_sensor_if_needed(preset_sid)
            else:
                if preset_sid:
                    logger.warning(
                        "RTSP session %s: sensor_id %r not found in VIOS — registering '%s' via rtsp_url",
                        session_id, preset_sid, camera_name,
                    )
                logger.info(
                    "RTSP session %s: registering sensor '%s' → %s",
                    session_id, camera_name, rtsp_url,
                )
                sid, was_created = await vios.register_sensor(
                    name=camera_name,
                    rtsp_url=rtsp_url,
                )
                if was_created:
                    resolved_sensor_ids.append(sid)
                    # VIOS auto-starts recording on new sensor registration
                    # (alwaysRecording), so include in cleanup. Pre-existing
                    # sensors reused via name lookup are NOT ours to stop.
                    sess["sensors_started_by_us"].append(sid)
                    # Track for deregistration at session delete time.
                    sess["sensors_registered_by_us"].append(sid)
                    logger.info(
                        "RTSP session %s: sensor '%s' registered as %s (VIOS auto-started)",
                        session_id, camera_name, sid,
                    )
                else:
                    resolved_sensor_ids.append(sid)
                    registered_camera_name = await vios.get_sensor_name(sid)
                    _validate_rtsp_camera_name_matches_vios(
                        requested_name=camera_name,
                        vios_sensor_name=registered_camera_name,
                        sensor_id=sid,
                    )
                    logger.info(
                        "RTSP session %s: reusing existing VIOS sensor %s for '%s'",
                        session_id, sid, camera_name,
                    )
                    await _start_preexisting_sensor_if_needed(sid)

        sess["sensor_ids"] = resolved_sensor_ids

        # ------------------------------------------------------------------ #
        # Step 2 – Verify all sensors are actively recording before opening
        #          the time window. Poll up to _VERIFY_RETRIES times to
        #          accommodate VIOS pipeline latency (newly-registered
        #          sensors take a moment before /record/status reflects them).
        # ------------------------------------------------------------------ #
        _ACTIVE = ("alwaysOn", "on", "user")
        _VERIFY_RETRIES = 5
        _VERIFY_INTERVAL = 1.0  # seconds between verify polls
        unconfirmed = list(resolved_sensor_ids)
        for attempt in range(_VERIFY_RETRIES):
            statuses = await vios.get_record_status()
            unconfirmed = [
                sid for sid in unconfirmed
                if statuses.get(sid, {}).get("recording_status") not in _ACTIVE
            ]
            if not unconfirmed:
                break
            # No sleep after the final poll — we would just throw away the wait.
            if attempt == _VERIFY_RETRIES - 1:
                break
            logger.info(
                "RTSP session %s: waiting for sensors to start recording (attempt %d/%d): %s",
                session_id, attempt + 1, _VERIFY_RETRIES, unconfirmed,
            )
            await asyncio.sleep(_VERIFY_INTERVAL)
        if unconfirmed:
            raise RuntimeError(
                f"Sensors did not confirm recording after {_VERIFY_RETRIES} attempts: {unconfirmed}"
            )

        # ------------------------------------------------------------------ #
        # Step 3 – Note start time and enter RECORDING state
        # ------------------------------------------------------------------ #
        sess["recording_start_time"] = datetime.utcnow().isoformat() + "Z"
        sess["status"] = "RECORDING"
        logger.info(
            "RTSP session %s: capture window open at %s. Will close after %d seconds.",
            session_id, sess["recording_start_time"], sess["duration_seconds"],
        )

        # ------------------------------------------------------------------ #
        # Step 4 – Wait for requested duration (cancellable)
        # ------------------------------------------------------------------ #
        await asyncio.sleep(sess["duration_seconds"])

    except asyncio.CancelledError:
        # User called the stop endpoint
        logger.info("RTSP session %s: cancelled by user.", session_id)
        has_recording_window = bool(
            sess.get("recording_start_time") and sess.get("sensor_ids")
        )
        if has_recording_window:
            sess["status"] = "STOPPING"
            sess["recording_end_time"] = datetime.utcnow().isoformat() + "Z"
        else:
            # No usable capture window exists while sensor setup is still in
            # progress. Report cancellation immediately instead of implying
            # that recordings will be stopped and ingested.
            sess["status"] = "CANCELLED"
        try:
            stop_failures = await asyncio.shield(_stop_our_recordings())
        except asyncio.CancelledError:
            sess["status"] = "ERROR"
            sess["error_message"] = (
                "Service stopped while stopping VIOS recordings after RTSP capture cancellation."
            )
            raise
        if stop_failures:
            sess["stop_failures"] = stop_failures
            logger.warning(
                "RTSP session %s: failed to stop recording for sensors %s after cancellation.",
                session_id, stop_failures,
            )
        # A stopped capture with a valid recording window follows the same
        # automatic ingest path as a naturally completed capture. Stopping
        # during STARTING has no usable window and remains CANCELLED.
        if has_recording_window:
            try:
                # Do not shield ingest here. A second cancellation (for
                # example, server shutdown) must cancel the ingest coroutine
                # rather than leave it running after this task exits.
                await _ingest_rtsp_session(session_id)
            except asyncio.CancelledError:
                sess["status"] = "ERROR"
                sess["error_message"] = "Service stopped while automatic RTSP ingest was in progress."
                raise
            except Exception as exc:
                logger.error("RTSP session %s: automatic ingest failed after early stop – %s", session_id, exc)
            return
        return

    except Exception as exc:
        logger.error("RTSP session %s: error during capture – %s", session_id, exc)
        await asyncio.shield(_stop_our_recordings())  # best-effort; do not overwrite exc info on failure
        sess["status"] = "ERROR"
        sess["error_message"] = str(exc)
        return

    # ------------------------------------------------------------------ #
    # Step 5 – Note end time, stop recordings AMC MS started, then ingest
    # ------------------------------------------------------------------ #
    sess["recording_end_time"] = datetime.utcnow().isoformat() + "Z"
    stop_failures = await _stop_our_recordings()
    if stop_failures:
        sess["stop_failures"] = stop_failures
        logger.warning(
            "RTSP session %s: failed to stop recording for sensors %s after completion.",
            session_id, stop_failures,
        )
    logger.info(
        "RTSP session %s: capture complete [%s → %s]; starting automatic ingest.",
        session_id, sess["recording_start_time"], sess["recording_end_time"],
    )
    try:
        await _ingest_rtsp_session(session_id)
    except asyncio.CancelledError:
        sess["status"] = "ERROR"
        sess["error_message"] = "Service stopped while automatic RTSP ingest was in progress."
        raise
    except Exception as exc:
        logger.error("RTSP session %s: automatic ingest failed – %s", session_id, exc)


# ---------------------------------------------------------------------------
# Endpoint helpers
# ---------------------------------------------------------------------------

def _get_rtsp_session_or_404(project_id: str, session_id: str) -> dict:
    """Return the session dict or raise 404."""
    sess = app.state.rtsp_sessions.get(session_id)
    if sess is None or sess.get("project_id") != project_id:
        raise HTTPException(status_code=404, detail=f"RTSP session '{session_id}' not found for project '{project_id}'")
    return sess


async def _deregister_session_sensors(sess: dict) -> list:
    """Best-effort deregister the sensors AMC MS created for this session.

    Pre-existing sensors that were merely reused are NOT touched — only the
    UUIDs in ``sensors_registered_by_us`` (populated when register_sensor
    returned ``was_created=True``). Failures are logged and collected but do
    not raise, so callers can proceed with session cleanup regardless.

    Returns the list of sensor IDs that failed to deregister.
    """
    sids = sess.get("sensors_registered_by_us", [])
    if not sids:
        return []
    vios = ViosClient(
        base_url=sess.get("vios_base_url") or VIOS_BASE_URL_ENV,
        token=sess.get("vios_token"),
        ssl_verify=sess.get("ssl_verify", False),
    )
    results = await asyncio.gather(
        *[vios.deregister_sensor(sid) for sid in sids],
        return_exceptions=True,
    )
    failed = []
    for sid, result in zip(sids, results):
        if result is True:
            logger.info("Deregistered VIOS sensor %s (owned by session %s).",
                        sid, sess.get("session_id"))
        else:
            logger.warning("Failed to deregister VIOS sensor %s (session %s): %r",
                           sid, sess.get("session_id"), result)
            failed.append(sid)
    return failed


# ---------------------------------------------------------------------------
# POST /rtsp/capture/{project_id}  – start capture
# ---------------------------------------------------------------------------

@app.post(
    f"{API_VERSION_PREFIX}/rtsp/capture/{{project_id}}",
    tags=["RTSP Capture"],
    summary="Start RTSP capture via VIOS",
    description=(
        "Register the supplied RTSP streams as VIOS sensors, start recording, "
        "wait for `duration_seconds`, stop AMC-owned recordings, then automatically "
        "download the resulting clips into the project. Poll the session status "
        "until it reaches `INGESTED` or `ERROR`."
    ),
)
async def start_rtsp_capture(
    project_id: str,
    request: RtspCaptureRequest,
) -> RtspCaptureResponse:
    if not VIOS_BASE_URL_ENV:
        raise HTTPException(
            status_code=503,
            detail="RTSP capture is disabled: VIOS_BASE_URL env var is not set.",
        )
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")
    if app.state.projects[project_id]["project_state"] == "RUNNING":
        raise HTTPException(status_code=400, detail="Calibration is running. Starting RTSP capture is not allowed.")
    if app.state.projects[project_id].get("input_source_mode") == "manual":
        raise HTTPException(
            status_code=409,
            detail="Project contains manually uploaded videos. Remove them before starting RTSP capture.",
        )
    active_states = ("STARTING", "RECORDING", "STOPPING", "INGESTING")
    active_sessions = [
        sid for sid, session in app.state.rtsp_sessions.items()
        if session.get("project_id") == project_id and session.get("status") in active_states
    ]
    if active_sessions:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Project already has active RTSP session(s): {active_sessions}. "
                "Wait for completion or stop the active capture before starting another."
            ),
        )
    if not request.streams:
        raise HTTPException(status_code=400, detail="At least one RTSP stream must be specified.")
    if request.duration_seconds < MIN_RTSP_DURATION_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=f"duration_seconds must be at least {MIN_RTSP_DURATION_SECONDS}.",
        )
    if request.duration_seconds > MAX_RTSP_DURATION_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"duration_seconds must not exceed {MAX_RTSP_DURATION_SECONDS}."
            ),
        )

    session_id = f"rtsp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{randint(1000, 9999)}"
    session_data = RtspCaptureSession(
        session_id=session_id,
        project_id=project_id,
        status="STARTING",
        streams=request.streams,
        duration_seconds=request.duration_seconds,
        sensor_ids=[],
        created_at=datetime.utcnow().isoformat() + "Z",
    ).model_dump()
    # Private session-only fields (not exposed via RtspCaptureSession responses).
    # vios_base_url is snapshot here so a later env var change does not affect
    # in-flight sessions or ingest of already-captured data.
    session_data["vios_base_url"] = VIOS_BASE_URL_ENV
    session_data["vios_token"] = request.vios_token
    session_data["ssl_verify"] = request.ssl_verify

    app.state.rtsp_sessions[session_id] = session_data

    # Launch background asyncio task
    task = asyncio.create_task(_run_rtsp_capture(session_id))
    app.state.rtsp_tasks[session_id] = task

    logger.info(
        "RTSP session %s created for project %s (%d streams, %ds)",
        session_id, project_id, len(request.streams), request.duration_seconds,
    )

    return RtspCaptureResponse(
        code=0,
        message=f"RTSP capture session '{session_id}' started.",
        session=RtspCaptureSession(**{k: v for k, v in session_data.items() if k in RtspCaptureSession.model_fields}),
    )


# ---------------------------------------------------------------------------
# GET /rtsp/capture/{project_id}/{session_id}  – session status
# ---------------------------------------------------------------------------

@app.get(
    f"{API_VERSION_PREFIX}/rtsp/capture/{{project_id}}/{{session_id}}",
    tags=["RTSP Capture"],
    summary="Get RTSP capture session status",
)
async def get_rtsp_capture_status(project_id: str, session_id: str) -> RtspCaptureResponse:
    sess = _get_rtsp_session_or_404(project_id, session_id)
    return RtspCaptureResponse(
        code=0,
        message=f"Session status: {sess['status']}",
        session=RtspCaptureSession(**{k: v for k, v in sess.items() if k in RtspCaptureSession.model_fields}),
    )


# ---------------------------------------------------------------------------
# POST /rtsp/capture/{project_id}/{session_id}/stop  – stop early
# ---------------------------------------------------------------------------

@app.post(
    f"{API_VERSION_PREFIX}/rtsp/capture/{{project_id}}/{{session_id}}/stop",
    tags=["RTSP Capture"],
    summary="Stop an ongoing RTSP capture early",
    description="Cancels the recording timer and stops all VIOS recordings for this session.",
)
async def stop_rtsp_capture(project_id: str, session_id: str) -> RtspCaptureResponse:
    sess = _get_rtsp_session_or_404(project_id, session_id)

    if sess["status"] not in ("STARTING", "RECORDING"):
        raise HTTPException(
            status_code=400,
            detail=f"Session is '{sess['status']}' – can only stop a STARTING or RECORDING session.",
        )

    # Reject stops that would produce a recording shorter than the minimum
    # duration — clips under MIN_RTSP_DURATION_SECONDS are not usable for
    # calibration. During STARTING, no recording has actually begun yet
    # (verify loop hasn't stamped recording_start_time), so cancellation is
    # always allowed there and the attempt is simply abandoned.
    if sess["status"] == "RECORDING":
        rst = sess.get("recording_start_time")
        if rst:
            start_dt = datetime.fromisoformat(rst.rstrip("Z"))
            elapsed = (datetime.utcnow() - start_dt).total_seconds()
            if elapsed < MIN_RTSP_DURATION_SECONDS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Recording has only been active for {elapsed:.1f}s; "
                        f"minimum duration is {MIN_RTSP_DURATION_SECONDS}s for a usable clip. "
                        "Wait and retry."
                    ),
                )

    was_recording = sess["status"] == "RECORDING"
    task = app.state.rtsp_tasks.get(session_id)
    if task and not task.done():
        # The lifecycle task stops VIOS recording and ingests clips. Do not wait
        # for download completion in this request: it can take minutes and
        # would make the stop endpoint time out.
        sess["status"] = "STOPPING" if was_recording else "CANCELLED"
        task.cancel()

    message = (
        "RTSP capture stop requested; recorded clips are being added to the project."
        if was_recording
        else "RTSP capture stop requested before recording began; no clips will be added."
    )
    return RtspCaptureResponse(
        code=0,
        message=message,
        session=RtspCaptureSession(**{k: v for k, v in sess.items() if k in RtspCaptureSession.model_fields}),
    )


async def _ingest_rtsp_session(session_id: str) -> list[str]:
    """Download one completed capture from VIOS and add clips to its project.

    Internal-only: capture lifecycle owns ingestion, so callers never need a
    second API request. Raises on failure after persisting the ERROR status.
    """
    sess = app.state.rtsp_sessions.get(session_id)
    if sess is None:
        raise RuntimeError(f"RTSP session '{session_id}' no longer exists.")

    def _fail_precondition(message: str) -> NoReturn:
        # Callers invoke ingest from active lifecycle states (STOPPING or
        # RECORDING). Persist failure here so a rejected precondition cannot
        # strand a session in an active state and block later captures.
        sess["status"] = "ERROR"
        sess["error_message"] = message
        raise RuntimeError(message)

    project_id = sess["project_id"]
    project = app.state.projects.get(project_id)
    if project is None:
        _fail_precondition(f"Project '{project_id}' no longer exists.")
    if project["project_state"] == "RUNNING":
        _fail_precondition("Calibration is running. Automatic RTSP ingest is not allowed.")
    if not sess.get("sensor_ids"):
        _fail_precondition("No sensors were registered in this session.")
    if not sess.get("recording_start_time") or not sess.get("recording_end_time"):
        _fail_precondition("Recording time range is not available. Cannot query VIOS storage.")

    sess["status"] = "INGESTING"
    vios = ViosClient(
        base_url=sess["vios_base_url"],
        token=sess.get("vios_token"),
        ssl_verify=sess.get("ssl_verify", False),
    )

    project_dir = Path(project["project_dir"])
    existing_count = project["video_files_count"]
    ingested_files: list = []
    camera_name_updates: dict[str, str] = {}

    try:
        for idx, sensor_id in enumerate(sess["sensor_ids"]):
            cam_index = existing_count + idx
            cam_label = f"cam_{cam_index:02d}"
            output_filename = f"{cam_label}.mp4"
            output_path = project_dir / output_filename

            # Resolve the actual clip window available in VIOS storage.
            # VIOS requires startTime/endTime to overlap an existing timeline entry.
            clip_window = await vios.find_clip_window(
                sensor_id=sensor_id,
                after=sess["recording_start_time"],
                before=sess["recording_end_time"],
            )
            if clip_window is None:
                raise ViosClientError(
                    f"No clip available in VIOS for sensor '{sensor_id}' "
                    f"in window [{sess['recording_start_time']} → {sess['recording_end_time']}]. "
                    "Check /vst/api/v1/storage/timelines on the VIOS server."
                )

            logger.info(
                "RTSP session %s: downloading sensor %s clip [%s → %s] to %s",
                session_id, sensor_id,
                clip_window["startTime"], clip_window["endTime"],
                output_path,
            )

            await vios.download_video(
                sensor_id=sensor_id,
                start_time=clip_window["startTime"],
                end_time=clip_window["endTime"],
                output_path=str(output_path),
            )

            # Resolve camera display name from the original stream config
            stream_cfgs = sess.get("streams", [])
            if idx < len(stream_cfgs):
                original_name = stream_cfgs[idx].get("camera_name") or output_filename
            else:
                original_name = output_filename
            if isinstance(original_name, str):
                original_name = strip_video_filename_extension(original_name)

            try:
                _require_1080p_video(output_path, display_name=original_name or output_filename)
            except ValueError as exc:
                try:
                    output_path.unlink(missing_ok=True)
                except Exception as cleanup_err:
                    logger.warning(
                        "RTSP session %s: failed to remove rejected clip %s: %s",
                        session_id,
                        output_path,
                        cleanup_err,
                    )
                logger.error(
                    "RTSP session %s: rejected ingested clip %s: %s",
                    session_id,
                    output_filename,
                    exc,
                )
                raise RuntimeError(str(exc)) from exc

            ingested_files.append(output_filename)
            camera_name_updates[cam_label] = original_name

            logger.info("RTSP session %s: ingested %s", session_id, output_filename)

    except ViosClientError as exc:
        for fname in ingested_files:
            try:
                (project_dir / fname).unlink(missing_ok=True)
            except Exception as cleanup_err:
                logger.warning(
                    "RTSP session %s: failed to clean up %s after VIOS error: %s",
                    session_id,
                    fname,
                    cleanup_err,
                )
        sess["status"] = "ERROR"
        sess["error_message"] = str(exc)
        raise RuntimeError(f"VIOS download failed: {exc}") from exc
    except Exception as exc:
        for fname in ingested_files:
            try:
                (project_dir / fname).unlink(missing_ok=True)
            except Exception as cleanup_err:
                logger.warning(
                    "RTSP session %s: failed to clean up %s after ingest error: %s",
                    session_id,
                    fname,
                    cleanup_err,
                )
        sess["status"] = "ERROR"
        sess["error_message"] = str(exc)
        raise RuntimeError(f"Ingest failed: {exc}") from exc

    # Commit project and session metadata only after every download completes.
    # On a partial VIOS failure, keeping these fields unchanged prevents the
    # next capture from reusing a stale count and duplicating project entries.
    project["video_files"].extend(ingested_files)
    project["camera_name_map"].update(camera_name_updates)
    new_count = existing_count + len(ingested_files)
    project["video_files_count"] = new_count
    project["input_source_mode"] = RTSP_INPUT_SOURCE_MODE
    # RTSP clips are source media. Their arrival invalidates verification,
    # rectification, and both calibration pipelines just like an upload.
    _invalidate_linear_media(project_id)

    # Commit ingested state
    sess["ingested_files"] = ingested_files
    sess["status"] = "INGESTED"
    logger.info(
        "RTSP session %s: ingestion complete – %d file(s) at %dx%d added to project %s.",
        session_id,
        len(ingested_files),
        REQUIRED_VIDEO_WIDTH,
        REQUIRED_VIDEO_HEIGHT,
        project_id,
    )

    return ingested_files


# ---------------------------------------------------------------------------
# GET /rtsp/sessions/{project_id}  – list sessions
# ---------------------------------------------------------------------------

@app.get(
    f"{API_VERSION_PREFIX}/rtsp/sessions/{{project_id}}",
    tags=["RTSP Capture"],
    summary="List all RTSP capture sessions for a project",
)
async def list_rtsp_sessions(project_id: str) -> RtspSessionListResponse:
    if project_id not in app.state.projects:
        raise HTTPException(status_code=404, detail="Project not found")

    sessions = [
        RtspCaptureSession(**{k: v for k, v in sess.items() if k in RtspCaptureSession.model_fields})
        for sess in app.state.rtsp_sessions.values()
        if sess.get("project_id") == project_id
    ]
    return RtspSessionListResponse(
        code=0,
        message=f"{len(sessions)} RTSP session(s) found.",
        sessions=sessions,
    )


# ---------------------------------------------------------------------------
# DELETE /rtsp/session/{project_id}/{session_id}  – delete session
# ---------------------------------------------------------------------------

@app.delete(
    f"{API_VERSION_PREFIX}/rtsp/session/{{project_id}}/{{session_id}}",
    tags=["RTSP Capture"],
    summary="Delete an RTSP capture session record",
    description="Removes the session metadata. Active (RECORDING/INGESTING) sessions cannot be deleted.",
)
async def delete_rtsp_session(project_id: str, session_id: str) -> StandardResponse:
    sess = _get_rtsp_session_or_404(project_id, session_id)

    if sess["status"] in ("STARTING", "RECORDING", "STOPPING", "INGESTING"):
        raise HTTPException(
            status_code=400,
            detail=f"Session is '{sess['status']}'. Stop the capture before deleting.",
        )

    # Best-effort deregister of sensors AMC MS created for this session.
    # Failures are logged; session removal still proceeds.
    deregister_failures = await _deregister_session_sensors(sess)

    del app.state.rtsp_sessions[session_id]
    app.state.rtsp_tasks.pop(session_id, None)

    if deregister_failures:
        return StandardResponse(
            code=0,
            message=(
                f"RTSP session '{session_id}' deleted. "
                f"Warning: failed to deregister VIOS sensors: {deregister_failures}"
            ),
        )
    return StandardResponse(code=0, message=f"RTSP session '{session_id}' deleted.")


#------------------------------------------------------------------------------------------------#

# Get the OpenAPI specification
@app.get(f"{API_VERSION_PREFIX}/openapi.yaml", response_class=Response, include_in_schema=False)
def get_openapi_yaml() -> Response:
    from io import StringIO
    openapi_spec = app.openapi()
    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump(openapi_spec, stream)
    yaml_content = stream.getvalue()
    return Response(content=yaml_content, media_type="text/yaml")


# Catch-all for React Router (must be LAST route!)
@app.get(f"{API_VERSION_PREFIX}/{{full_path:path}}", response_class=HTMLResponse, include_in_schema=False)
async def catch_all(full_path: str):
    # Serve index.html for any non-API route (enables client-side routing)
    index_file = Path(__file__).parent / "static" / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    raise HTTPException(status_code=404, detail="Not Found")
