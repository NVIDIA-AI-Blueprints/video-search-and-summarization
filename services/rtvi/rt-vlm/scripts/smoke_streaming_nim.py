# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise a remote streaming NIM through the RTVI decoder and model adapter."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import time

from PIL import Image

from common.chunk_info import ChunkInfo
from models.base_vlm_model import VlmGenerationConfig
from models.openai_compat.openai_compat_model import CompOpenAIModel
from vlm_pipeline.video_file_frame_getter import DefaultFrameSelector, VideoFileFrameGetter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", default="/opt/nvidia/rtvi/warmup_streams/its_264.mp4")
    parser.add_argument("--out", type=Path, required=True, help="New evidence directory")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    if os.environ.get("VIA_VLM_STREAMING_NIM_ENABLED", "").lower() not in ("true", "1"):
        raise ValueError("Set VIA_VLM_STREAMING_NIM_ENABLED=true")

    getter = VideoFileFrameGetter(
        DefaultFrameSelector(16, False), frame_width=448, frame_height=252,
        do_preprocess=False, data_type_int8=True, enable_jpeg_output=False,
        vlm_model_type="cosmos-reason3",
    )
    try:
        frames, _, _, error = getter.get_frames(
            ChunkInfo(file=args.video, start_pts=0, end_pts=-1), request_id="nim-smoke-decode"
        )
        if error or frames is None or len(frames) != 16:
            raise RuntimeError(f"Expected 16 decoded frames: {error}")
        frames = frames.detach().cpu()
        if str(frames.dtype) != "torch.uint8":
            raise RuntimeError("Decoder must return uint8 RGB")
        encoded = []
        for frame in frames:
            buffer = io.BytesIO()
            Image.fromarray(frame.numpy()).save(buffer, format="JPEG", quality=95)
            encoded.append(buffer.getvalue())
    finally:
        getter.destroy_pipeline()

    (args.out / "inputs.json").write_text(json.dumps({
        "video": args.video, "shape": list(frames.shape),
        "jpeg_sha256": [hashlib.sha256(frame).hexdigest() for frame in encoded],
        "endpoint": os.environ.get("VIA_VLM_ENDPOINT"),
        "model": os.environ.get("VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"),
    }, indent=2))
    model = CompOpenAIModel(max_batch_size=2)
    generation = VlmGenerationConfig(
        temperature=0, max_new_tokens=32, top_p=1, repetition_penalty=1,
        system_prompt="Describe the visible scene briefly.",
    )
    sessions = {}
    session_ids = set()
    records = []
    try:
        for names, turns in ((["A", "B"], 20), (["reopened"], 4)):
            for name in names:
                session = model.start_streaming_vlm_session(
                    name, "What is visible?", generation,
                    {"fps": 1, "window_frames": 8}, request_id=name,
                )
                sessions[name] = session
                if session.session_id in session_ids:
                    raise RuntimeError("NIM reused a session ID")
                session_ids.add(session.session_id)
            for turn in range(turns):
                pending = []
                for name in names:
                    begun = time.monotonic()
                    future = model.generate_streaming_vlm_step(
                        sessions[name], "What is visible?",
                        [ChunkInfo(streamId=name, chunkIdx=turn)],
                        video_frames=[[encoded[turn % 16]]],
                        video_frames_times=[[float(turn)]], generation_config=generation,
                    )
                    pending.append((name, future, begun))
                for name, future, begun in pending:
                    outputs = future.result(timeout=240)
                    if len(outputs) != 1 or not outputs[0].output.strip():
                        raise RuntimeError("NIM returned missing or empty output")
                    metrics = outputs[0].streaming_metrics or {}
                    if "frame_index" in metrics and metrics["frame_index"] != turn:
                        raise RuntimeError(f"Noncontiguous frame index: {metrics}")
                    records.append({
                        "stream": name, "turn": turn, "text": outputs[0].output,
                        "metrics": metrics, "elapsed_s": time.monotonic() - begun,
                    })
                    (args.out / "decisions.json").write_text(json.dumps(records, indent=2))
            for name in names:
                model.end_streaming_vlm_session(name, sessions[name])
                del sessions[name]
        if len(records) != 44:
            raise RuntimeError(f"Expected 44 outputs, got {len(records)}")
    finally:
        # Attempt every remote close even when an earlier close fails.
        close_errors = []
        try:
            for name, session in list(sessions.items()):
                try:
                    model.end_streaming_vlm_session(name, session)
                except Exception as error:
                    close_errors.append(error)
                finally:
                    del sessions[name]
        finally:
            model._shutdown_model()
        if close_errors:
            raise RuntimeError(f"Failed to close {len(close_errors)} NIM sessions") from close_errors[0]
    (args.out / "gate.json").write_text(json.dumps({
        "status": "passed", "outputs": 44, "unique_sessions": len(session_ids),
        "sessions_closed": True,
        "json_fallback": getattr(model, "_nim_json_frame_transport", False),
        "scope": "RTVI decoder/model API; not REST caption pipeline, accuracy or capacity",
    }, indent=2))
    print("PASS: 44 outputs, two concurrent sessions, reopen and teardown")


if __name__ == "__main__":
    main()
