import os
import sys
import time
import json
import logging
import random
import requests
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.parse import urljoin

# Add project root to path for imports
sys.path.append(str(Path(__file__).parent.parent.parent))

from edge_node.config_loader import load_edge_config, EdgeConfig
from edge_node.db.manager import DBManager, PendingUpload

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("vss_uploader")

# --- Global Setup ---

PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
SCHEMA_PATH = PROJECT_ROOT / "config" / "schema.json"
DB_PATH = PROJECT_ROOT / "vss_events.db"

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
            sys.exit(1)

    if db_manager is None:
        db_manager = DBManager(DB_PATH, PROJECT_ROOT / "edge_node" / "db" / "schema.sql")
        db_manager.initialize_db()
        logger.info(f"Database initialized at {DB_PATH}")

# --- Utility Functions ---

def calculate_sha256(filepath: Path) -> str:
    """Calculates the SHA256 checksum of a file."""
    hasher = hashlib.sha256()
    try:
        with open(filepath, 'rb') as file:
            while chunk := file.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()
    except FileNotFoundError:
        logger.error(f"File not found for checksum calculation: {filepath}")
        raise

def get_session_with_mtls(config: EdgeConfig) -> requests.Session:
    """Returns a requests session configured for mTLS if required."""
    session = requests.Session()
    if config.network.use_mtls:
        cert_paths = config.network.cert_paths
        # requests expects a tuple (client_cert, client_key)
        session.cert = (cert_paths.client_cert, cert_paths.client_key)
        # requests uses verify for CA bundle path
        session.verify = cert_paths.ca_cert
        logger.debug("Requests session configured with mTLS.")
    
    # TODO: Add logic for Bearer JWT if present (currently not in config)
    
    return session

# --- Uploader Logic ---

class Uploader:
    
    def __init__(self):
        load_config_and_db()
        self.config = config
        self.db_manager = db_manager
        self.session = get_session_with_mtls(self.config)
        self.is_running = True

    def _request_presigned_url(self, upload: PendingUpload, file_path: Path) -> Dict[str, Any]:
        """POST to presigned_endpoint to get upload_url and final_url."""
        
        presigned_url = urljoin(self.config.network.api_base, self.config.upload.presigned_endpoint)
        
        # Determine content type (simple guess for now)
        content_type = "video/mp4" if file_path.suffix.lower() == ".mp4" else "application/octet-stream"
        
        payload = {
            "tenant_id": self.config.device.tenant_id,
            "device_id": self.config.device.device_id,
            "event_id": upload.event_id,
            "filename": file_path.name,
            "size_bytes": file_path.stat().st_size,
            "content_type": content_type
        }
        
        headers = {"Event-ID": upload.event_id}
        
        logger.info(f"Requesting presigned URL for {upload.upload_id}...")
        response = self.session.post(
            presigned_url, 
            json=payload, 
            headers=headers, 
            timeout=self.config.network.api_timeout_seconds
        )
        response.raise_for_status()
        return response.json()

    def _upload_file(self, upload_url: str, file_path: Path, checksum: str):
        """PUT the file to the presigned upload_url."""
        
        # Use streaming upload (requests handles chunking for file-like objects)
        with open(file_path, 'rb') as f:
            headers = {
                "Content-Type": "video/mp4", # Assuming MP4 for clips
                "x-amz-checksum-sha256": checksum # For S3 compatibility
            }
            logger.info(f"Uploading file {file_path.name} to presigned URL...")
            response = self.session.put(
                upload_url, 
                data=f, 
                headers=headers,
                timeout=None # Uploads can take a long time
            )
            response.raise_for_status()
            logger.info(f"Upload of {file_path.name} successful.")

    def _complete_upload(self, upload: PendingUpload, final_url: str, checksum: str):
        """POST to upload_complete_endpoint."""
        
        complete_url = urljoin(self.config.network.api_base, self.config.upload.upload_complete_endpoint)
        
        payload = {
            "upload_id": upload.upload_id,
            "event_id": upload.event_id,
            "final_url": final_url,
            "checksum": checksum
        }
        
        headers = {"Event-ID": upload.event_id}
        
        logger.info(f"Notifying server of upload completion for {upload.upload_id}...")
        response = self.session.post(
            complete_url, 
            json=payload, 
            headers=headers, 
            timeout=self.config.network.api_timeout_seconds
        )
        response.raise_for_status()

    def _post_metadata(self, upload: PendingUpload, final_url: str):
        """POST the full event metadata to metadata_endpoint."""
        
        metadata_url = urljoin(self.config.network.api_base, self.config.upload.metadata_endpoint)
        
        # Retrieve the full event JSON from the DB
        with self.db_manager.SessionLocal() as session:
            event = session.get(self.db_manager.Event, upload.event_id)
            if not event:
                raise ValueError(f"Event {upload.event_id} not found in DB.")
            
            metadata = json.loads(event.json)
            metadata["clip_url"] = final_url
            metadata["upload_id"] = upload.upload_id
        
        headers = {"Event-ID": upload.event_id}
        
        logger.info(f"Posting metadata for event {upload.event_id}...")
        response = self.session.post(
            metadata_url, 
            json=metadata, 
            headers=headers, 
            timeout=self.config.network.api_timeout_seconds
        )
        response.raise_for_status()

    def process_upload(self, upload: PendingUpload):
        """Handles the full upload lifecycle for a single pending upload."""
        
        file_path = Path(upload.filepath)
        if not file_path.exists():
            logger.error(f"Clip file not found: {file_path}. Marking as FAILED.")
            self.db_manager.update_upload_status(upload.upload_id, "FAILED")
            return

        try:
            # 1. Calculate Checksum
            checksum = calculate_sha256(file_path)
            
            # 2. Request Presigned URL
            presigned_data = self._request_presigned_url(upload, file_path)
            upload_url = presigned_data.get("upload_url")
            final_url = presigned_data.get("final_url")
            upload_id = presigned_data.get("upload_id", upload.upload_id) # Use server-provided ID if available
            
            if not upload_url or not final_url:
                raise ValueError("Presigned URL response missing 'upload_url' or 'final_url'.")

            # Update status to PROCESSING
            self.db_manager.update_upload_status(upload_id, "PROCESSING", checksum=checksum)

            # 3. Upload File
            self._upload_file(upload_url, file_path, checksum)

            # 4. Complete Upload Notification
            self._complete_upload(upload, final_url, checksum)

            # 5. Post Metadata
            self._post_metadata(upload, final_url)

            # 6. Final Status Update
            self.db_manager.update_upload_status(upload_id, "UPLOADED", final_url=final_url)
            logger.info(f"Successfully uploaded and processed event {upload.event_id}.")

        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code
            logger.error(f"HTTP Error during upload for {upload.upload_id}: {e}")
            self._handle_failure(upload, status_code)
        except Exception as e:
            logger.error(f"General Error during upload for {upload.upload_id}: {e}")
            self._handle_failure(upload, 500)

    def _handle_failure(self, upload: PendingUpload, status_code: int):
        """Handles retry logic and final failure state."""
        
        new_attempts = upload.attempts + 1
        
        if new_attempts >= self.config.upload.max_retries:
            logger.error(f"Upload {upload.upload_id} failed after {new_attempts} attempts. Marking as FAILED.")
            self.db_manager.update_upload_status(upload.upload_id, "FAILED", attempts=new_attempts)
            return

        # Retry logic: 5xx errors should retry, 4xx errors should not (unless specific)
        if status_code >= 500:
            # Exponential backoff with jitter
            backoff_time = self.config.upload.retry_backoff_seconds * (2 ** (new_attempts - 1))
            jitter = random.random() * self.config.upload.retry_backoff_seconds
            wait_time = min(backoff_time + jitter, 3600) # Cap at 1 hour
            
            logger.warning(f"Server error ({status_code}). Retrying {new_attempts}/{self.config.upload.max_retries} in {wait_time:.2f}s.")
            time.sleep(wait_time)
            
            # Update status to PENDING_UPLOAD (or a dedicated RETRY status) and increment attempts
            self.db_manager.update_upload_status(upload.upload_id, "PENDING_UPLOAD", attempts=new_attempts)
        else:
            # Client error (4xx) - usually means a configuration or data issue. Mark as FAILED.
            logger.error(f"Client error ({status_code}). Marking upload {upload.upload_id} as FAILED.")
            self.db_manager.update_upload_status(upload.upload_id, "FAILED", attempts=new_attempts)

    def run_loop(self):
        """Main service loop for polling and processing uploads."""
        logger.info("Uploader service started.")
        while self.is_running:
            try:
                pending_uploads = self.db_manager.get_pending_uploads(limit=10)
                
                if not pending_uploads:
                    logger.debug("No pending uploads found. Sleeping.")
                    time.sleep(5)
                    continue
                
                logger.info(f"Found {len(pending_uploads)} pending uploads to process.")
                for upload in pending_uploads:
                    self.process_upload(upload)
                    
            except Exception as e:
                logger.critical(f"Critical error in uploader loop: {e}. Restarting loop in 10s.")
                time.sleep(10)

    def shutdown(self):
        self.is_running = False
        logger.info("Uploader service shutting down.")

# --- Main Entry Point ---

if __name__ == "__main__":
    uploader = Uploader()
    try:
        uploader.run_loop()
    except KeyboardInterrupt:
        uploader.shutdown()
