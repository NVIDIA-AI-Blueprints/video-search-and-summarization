import json
import uuid
import logging
import tarfile
import hashlib
from flask import Flask, request, jsonify, send_file
from pathlib import Path
from typing import Dict, Any

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mock_central_server")

# --- Global State for Mock Server ---
MOCK_DB: Dict[str, Any] = {}
UPLOAD_ID_COUNTER = 0

# --- Flask App Setup ---
app = Flask(__name__)

# --- Utility Functions ---

def get_next_upload_id():
    global UPLOAD_ID_COUNTER
    UPLOAD_ID_COUNTER += 1
    return f"mock-upload-{UPLOAD_ID_COUNTER}"

# --- API Endpoints ---

@app.route("/api/v1/upload-request", methods=["POST"])
def upload_request():
    data = request.get_json()
    log.info(f"Received /upload-request: {data}")
    event_id = data.get("event_id")
    filename = data.get("filename")
    
    if not event_id or not filename:
        return jsonify({"error": "Missing event_id or filename"}), 400

    upload_id = get_next_upload_id()
    
    MOCK_DB[upload_id] = {
        "event_id": event_id,
        "filename": filename,
        "status": "REQUESTED",
        "data": data
    }
    
    mock_upload_url = f"http://localhost:5000/mock-upload/{upload_id}"
    final_url = f"https://cdn.example.com/{data['tenant_id']}/{data['device_id']}/{event_id}/{filename}"
    
    response = {
        "upload_id": upload_id,
        "upload_url": mock_upload_url,
        "final_url": final_url
    }
    log.info(f"Responding to /upload-request with: {response}")
    return jsonify(response), 200

@app.route("/mock-upload/<upload_id>", methods=["PUT"])
def mock_upload(upload_id):
    log.info(f"Received PUT on /mock-upload/{upload_id}")
    if upload_id not in MOCK_DB:
        return jsonify({"error": "Invalid upload_id"}), 404
    
    content_length = request.content_length
    MOCK_DB[upload_id]["status"] = "UPLOADED"
    MOCK_DB[upload_id]["size"] = content_length
    
    return jsonify({"message": "Upload successful"}), 200

@app.route("/api/v1/upload-complete", methods=["POST"])
def upload_complete():
    data = request.get_json()
    log.info(f"Received /upload-complete: {data}")
    upload_id = data.get("upload_id")
    
    if upload_id not in MOCK_DB:
        return jsonify({"error": "Invalid upload_id"}), 404
    
    MOCK_DB[upload_id]["status"] = "COMPLETE_NOTIFIED"
    MOCK_DB[upload_id]["checksum"] = data.get("checksum")
    
    return jsonify({"message": "Upload completion acknowledged"}), 200

@app.route("/api/v1/metadata", methods=["POST"])
def metadata_post():
    data = request.get_json()
    log.info(f"Received /metadata: {data}")
    event_id = data.get("event_id")
    
    if not event_id:
        return jsonify({"error": "Missing event_id"}), 400
    
    upload_id = next((k for k, v in MOCK_DB.items() if v.get("event_id") == event_id), None)
    
    if upload_id and upload_id in MOCK_DB:
        MOCK_DB[upload_id]["status"] = "METADATA_POSTED"
        MOCK_DB[upload_id]["metadata"] = data
        return jsonify({"message": "Metadata received"}), 200
    
    return jsonify({"error": "Event not found"}), 404

@app.route("/api/v1/training-packages", methods=["GET"])
def training_packages():
    package_name = "mock-model-v2.0.tar.gz"
    package_path = Path("/tmp") / package_name
    
    with tarfile.open(package_path, "w:gz") as tar:
        dummy_file = Path("/tmp/dummy_model_file.txt")
        dummy_file.write_text("This is a mock model file.")
        tar.add(dummy_file, arcname="model/dummy_model_file.txt")
        dummy_file.unlink()
        
    sha256 = hashlib.sha256(package_path.read_bytes()).hexdigest()
    signature = "mock-signature-12345"
    
    return jsonify([
        {
            "id": "mock-detector",
            "version": "v2.0",
            "download_url": f"http://localhost:5000/download/{package_name}",
            "sha256": sha256,
            "signature": signature
        }
    ]), 200

@app.route("/download/<filename>", methods=["GET"])
def download_file(filename):
    file_path = Path("/tmp") / filename
    if not file_path.exists():
        return jsonify({"error": "File not found"}), 404
    
    return send_file(file_path, mimetype='application/gzip', as_attachment=True, download_name=filename)

@app.route("/api/v1/knowledge/manifest", methods=["GET"])
def kb_manifest():
    return jsonify({
        "kb_version": "20251116.1",
        "delta_package_url": "http://localhost:5000/download/kb-delta-20251116.1.zip"
    }), 200

@app.route("/health")
def health_check():
    return jsonify({"status": "ok"})

def get_mock_db():
    return MOCK_DB

if __name__ == "__main__":
    DUMMY_CLIP_PATH = Path("/tmp/test_clip.mp4")
    DUMMY_CLIP_PATH.write_bytes(b"This is a mock video clip content for testing upload.")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
