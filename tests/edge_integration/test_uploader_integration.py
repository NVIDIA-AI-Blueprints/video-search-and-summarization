import pytest
import time
import json
import subprocess
import requests
from pathlib import Path
from datetime import datetime
from multiprocessing import Process
from edge_node.db.manager import DBManager, Event, PendingUpload
from edge_node.services.vss_aggregator import app as aggregator_app
from edge_node.services.vss_uploader import Uploader
from tests.edge_integration.mock_central_server import app as mock_server_app, get_mock_db

# --- Fixtures for Setup and Teardown ---

# Define paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "vss_events.db"
SCHEMA_PATH = PROJECT_ROOT / "edge_node" / "db" / "schema.sql"
DUMMY_CLIP_PATH = Path("/tmp/test_clip.mp4")

@pytest.fixture(scope="session", autouse=True)
def setup_dummy_clip():
    """Creates a dummy clip file for the uploader to use."""
    DUMMY_CLIP_PATH.write_bytes(b"This is a mock video clip content for testing upload.")
    yield
    if DUMMY_CLIP_PATH.exists():
        DUMMY_CLIP_PATH.unlink()

@pytest.fixture(scope="session")
def db_manager():
    """Provides a clean DBManager instance for testing."""
    if DB_PATH.exists():
        DB_PATH.unlink()
    manager = DBManager(DB_PATH, SCHEMA_PATH)
    manager.initialize_db()
    yield manager
    if DB_PATH.exists():
        DB_PATH.unlink()

@pytest.fixture(scope="session")
def mock_central_server():
    """Starts the mock central server in a separate process."""
    def run_server():
        # Use a simple Flask run for the mock server
        mock_server_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
        
    p = Process(target=run_server)
    p.start()
    time.sleep(2) # Give the server time to start
    yield
    p.terminate()
    p.join()

@pytest.fixture(scope="function")
def clean_mock_db():
    """Clears the mock central server's state before each test."""
    get_mock_db().clear()
    yield

# --- Test Data ---

TEST_EVENT_DATA = {
    "camera_id": "cam-01",
    "event_type": "motion",
    "timestamp": datetime.now().isoformat(),
    "local_clip_path": str(DUMMY_CLIP_PATH),
    "objects": [],
    "dense_caption": "A test event",
    "audio_text": None,
    "confidence": 0.9
}

# --- Tests ---

def test_uploader_end_to_end_success(db_manager, mock_central_server, clean_mock_db):
    """
    Tests the full lifecycle of an event:
    1. Insert event into DB (simulated by Aggregator)
    2. Uploader picks up the event
    3. Uploader requests presigned URL (Mock Server)
    4. Uploader uploads file (Mock Server)
    5. Uploader notifies upload complete (Mock Server)
    6. Uploader posts metadata (Mock Server)
    7. DB status is updated to UPLOADED
    """
    
    # 1. Insert event into DB (Simulate Aggregator action)
    event_data_with_id = TEST_EVENT_DATA.copy()
    event_data_with_id["event_id"] = "evt-test-001"
    
    event_id, upload_id = db_manager.insert_event(event_data_with_id, str(DUMMY_CLIP_PATH))
    
    # Ensure initial state is PENDING_UPLOAD
    with db_manager.SessionLocal() as session:
        initial_upload = session.get(PendingUpload, upload_id)
        assert initial_upload.status == "PENDING_UPLOAD"
        
    # 2. Initialize and run the Uploader once
    uploader = Uploader()
    
    # Force a single run of the processing loop
    pending_uploads = db_manager.get_pending_uploads(limit=1)
    assert len(pending_uploads) == 1
    
    uploader.process_upload(pending_uploads[0])
    
    # 7. Assert final state in local DB
    with db_manager.SessionLocal() as session:
        final_upload = session.get(PendingUpload, upload_id)
        final_event = session.get(Event, event_id)
        
        assert final_upload.status == "UPLOADED"
        assert final_event.status == "UPLOADED"
        assert final_upload.checksum is not None
        assert final_upload.final_url is not None
        
    # 7. Assert final state in Mock Central Server
    mock_db = get_mock_db()
    
    # Find the upload ID created by the mock server
    mock_upload_id = next(k for k, v in mock_db.items() if v.get("event_id") == event_id)
    
    assert mock_db[mock_upload_id]["status"] == "METADATA_POSTED"
    assert mock_db[mock_upload_id]["checksum"] == final_upload.checksum
    assert mock_db[mock_upload_id]["size"] == DUMMY_CLIP_PATH.stat().st_size
    assert mock_db[mock_upload_id]["metadata"]["event_id"] == event.event_id
    assert "clip_url" in mock_db[mock_upload_id]["metadata"]
    
    print("Uploader end-to-end test passed successfully.")

# To run this test:
# 1. Ensure you are in the project root directory.
# 2. Run: pytest tests/edge_integration/test_uploader_integration.py
