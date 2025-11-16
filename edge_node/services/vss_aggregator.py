import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel, Field

# Local imports
from edge_node.config_loader import load_edge_config, EdgeConfig
from edge_node.db.manager import DBManager, Event, PendingUpload

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("vss_aggregator")

# --- Global Setup ---

# Define paths relative to the project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
SCHEMA_PATH = PROJECT_ROOT / "config" / "schema.json"
DB_PATH = PROJECT_ROOT / "vss_events.db" # Using project root for simplicity, should be /var/lib/vss/vss_events.db in production

# Initialize Config and DB Manager (will be loaded on startup)
config: Optional[EdgeConfig] = None
db_manager: Optional[DBManager] = None

def load_config_and_db():
    """Loads configuration and initializes the database manager."""
    global config, db_manager
    if config is None:
        try:
            config = load_edge_config(CONFIG_PATH, SCHEMA_PATH)
            logger.info("Configuration loaded successfully.")
        except Exception as e:
            logger.critical(f"Failed to load configuration: {e}")
            raise RuntimeError("Configuration failed to load.") from e

    if db_manager is None:
        # Use a temporary path for the DB for now, will be mounted in Docker
        db_manager = DBManager(DB_PATH, PROJECT_ROOT / "edge_node" / "db" / "schema.sql")
        db_manager.initialize_db()
        logger.info(f"Database initialized at {DB_PATH}")

# --- Pydantic Schemas for API ---

class EventIn(BaseModel):
    """Schema for an incoming event from vss_cv or vss_ingest."""
    camera_id: str = Field(..., description="The ID of the camera that generated the event.")
    event_type: str = Field(..., description="Type of event, e.g., 'motion', 'object_detection'.")
    timestamp: datetime = Field(..., description="Timestamp of the event.")
    local_clip_path: str = Field(..., description="Local path to the associated video clip.")
    objects: List[Dict[str, Any]] = Field(default_factory=list, description="List of detected objects.")
    dense_caption: Optional[str] = Field(None, description="AI-generated dense caption.")
    audio_text: Optional[str] = Field(None, description="Transcribed audio text.")
    confidence: float = Field(..., description="Confidence score of the event.")

class EventOut(BaseModel):
    """Schema for an event returned by the API."""
    event_id: str
    camera_id: str
    event_type: str
    timestamp: datetime
    status: str
    local_clip_path: str
    
class StatusUpdate(BaseModel):
    upload_id: str
    status: str = Field(..., pattern="^(PROCESSING|FAILED|UPLOADED)$")
    final_url: Optional[str] = None
    checksum: Optional[str] = None
    attempts: Optional[int] = None

# --- FastAPI Application ---

app = FastAPI(
    title="VSS Aggregator Service",
    on_startup=[load_config_and_db]
)

# Dependency to get the DB session
def get_db_session():
    if db_manager is None:
        raise HTTPException(status_code=503, detail="Database not initialized.")
    yield from db_manager.get_session()

# --- Event Builder Logic ---

def build_event_json(event_in: EventIn) -> Dict[str, Any]:
    """
    Constructs the full event JSON structure from the incoming data and config.
    """
    if config is None:
        raise RuntimeError("Configuration not loaded.")

    # Generate a unique event ID
    event_id = f"evt-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"

    event_json = {
        "tenant_id": config.device.tenant_id,
        "device_id": config.device.device_id,
        "camera_id": event_in.camera_id,
        "event_id": event_id,
        "timestamp": event_in.timestamp.isoformat(),
        "event_type": event_in.event_type,
        "objects": event_in.objects,
        "dense_caption": event_in.dense_caption,
        "audio_text": event_in.audio_text,
        "local_clip_path": event_in.local_clip_path,
        "confidence": event_in.confidence
    }
    return event_json

# --- API Endpoints ---

@app.post("/events/new", status_code=201, response_model=EventOut)
async def create_new_event(event_in: EventIn):
    """
    Receives a new event, builds the full metadata, and enqueues it for upload.
    """
    try:
        event_json = build_event_json(event_in)
        
        # Insert into DB
        event, upload = db_manager.insert_event(event_json, event_in.local_clip_path)
        
        return EventOut(
            event_id=event.event_id,
            camera_id=event_in.camera_id,
            event_type=event_in.event_type,
            timestamp=event_in.timestamp,
            status=event.status,
            local_clip_path=event_in.local_clip_path
        )
    except Exception as e:
        logger.error(f"Error creating new event: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")

@app.get("/events/pending", response_model=List[Dict[str, Any]])
async def get_pending_events():
    """
    Retrieves a list of pending uploads for the uploader service to process.
    """
    try:
        pending_uploads = db_manager.get_pending_uploads(limit=100)
        
        # Return the upload details
        return [upload.to_dict() for upload in pending_uploads]
    except Exception as e:
        logger.error(f"Error retrieving pending events: {e}")
        raise HTTPException(status_code=500, detail="Internal server error.")

@app.post("/events/mark_status")
async def mark_event_status(update: StatusUpdate):
    """
    Marks an event's upload status. Used by the uploader service.
    """
    try:
        db_manager.update_upload_status(
            upload_id=update.upload_id,
            status=update.status,
            final_url=update.final_url,
            checksum=update.checksum,
            attempts=update.attempts
        )
        return {"message": f"Upload {update.upload_id} marked as {update.status}"}
    except Exception as e:
        logger.error(f"Error marking event status: {e}")
        raise HTTPException(status_code=500, detail="Internal server error.")

@app.get("/health")
async def health_check():
    """Basic health check endpoint."""
    return {"status": "ok", "db_path": str(DB_PATH), "config_loaded": config is not None}

# --- Main Entry Point ---

if __name__ == "__main__":
    import uvicorn
    # The aggregator service runs on port 8002
    uvicorn.run(app, host="0.0.0.0", port=8002)
