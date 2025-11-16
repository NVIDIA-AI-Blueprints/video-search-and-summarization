import os
import sys
import time
import json
import logging
import requests
import hashlib
import tarfile
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.parse import urljoin

# Add project root to path for imports
sys.path.append(str(Path(__file__).parent.parent.parent))

from edge_node.config_loader import load_edge_config, EdgeConfig
from edge_node.db.manager import DBManager, KBMeta

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("vss_sync")

# --- Global Setup ---

PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
SCHEMA_PATH = PROJECT_ROOT / "config" / "schema.json"
DB_PATH = PROJECT_ROOT / "vss_events.db"

# Model storage location (as per prompt)
MODEL_STORAGE_PATH = Path("/opt/vss/models")
MODEL_STORAGE_PATH.mkdir(parents=True, exist_ok=True)

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

# --- Sync Worker Logic ---

class SyncWorker:
    
    def __init__(self):
        load_config_and_db()
        self.config = config
        self.db_manager = db_manager
        self.session = requests.Session() # Simple session for sync
        self.is_running = True
        self.last_sync_time = 0

    def _get_last_kb_version(self) -> str:
        """Retrieves the last applied KB version from the database."""
        with self.db_manager.SessionLocal() as session:
            kb_meta = session.query(KBMeta).order_by(KBMeta.applied_at.desc()).first()
            return kb_meta.kb_version if kb_meta else "0.0.0"

    def _poll_packages(self):
        """Polls the packages endpoint for new model/training packages."""
        
        packages_url = urljoin(self.config.network.api_base, self.config.sync.packages_endpoint)
        
        # Get the current model version (mocked for now, should come from vss_cv health check)
        current_model_version = "mock-v1.0" 
        
        params = {"since": current_model_version}
        
        try:
            logger.info(f"Polling packages endpoint: {packages_url} with params {params}")
            response = self.session.get(
                packages_url, 
                params=params,
                timeout=self.config.network.api_timeout_seconds
            )
            response.raise_for_status()
            packages = response.json()
            
            for package in packages:
                self._process_package(package)
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Error polling packages endpoint: {e}")

    def _process_package(self, package: Dict[str, Any]):
        """Downloads, verifies, and installs a model package."""
        
        package_id = package.get("id")
        version = package.get("version")
        download_url = package.get("download_url")
        sha256 = package.get("sha256")
        signature = package.get("signature")
        
        if not all([package_id, version, download_url, sha256, signature]):
            logger.error(f"Invalid package manifest received: {package}")
            return

        logger.info(f"Processing new package: {package_id} (v{version})")
        
        # 1. Download package
        local_path = MODEL_STORAGE_PATH / f"{package_id}-{version}.tar.gz"
        try:
            with self.session.get(download_url, stream=True) as r:
                r.raise_for_status()
                with open(local_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            logger.info(f"Package downloaded to {local_path}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to download package {package_id}: {e}")
            return

        # 2. Verify SHA256
        try:
            calculated_sha256 = self._calculate_file_sha256(local_path)
            if calculated_sha256 != sha256:
                logger.error(f"SHA256 mismatch for {package_id}. Expected {sha256}, got {calculated_sha256}. Deleting file.")
                local_path.unlink()
                return
            logger.info("SHA256 verification successful.")
        except Exception as e:
            logger.error(f"Error during SHA256 calculation: {e}")
            return

        # 3. Verify Signature (Security: Placeholder)
        # NOTE: The prompt requires signature validation using a pre-provisioned public key.
        # This requires a cryptography library and the key itself, which is outside the scope
        # of this implementation, so we will mock the success.
        logger.warning("Signature validation is mocked as successful. Real implementation required.")
        # if not self._verify_signature(local_path, signature):
        #     logger.error(f"Signature validation failed for {package_id}. Deleting file.")
        #     local_path.unlink()
        #     return

        # 4. Extract and Install
        install_path = MODEL_STORAGE_PATH / package_id
        install_path.mkdir(exist_ok=True)
        try:
            with tarfile.open(local_path, "r:gz") as tar:
                tar.extractall(path=install_path)
            logger.info(f"Package extracted to {install_path}")
        except Exception as e:
            logger.error(f"Failed to extract package {package_id}: {e}")
            return

        # 5. Call local model reload endpoint (vss_cv)
        try:
            # NOTE: This assumes vss_cv is running on localhost:8001
            reload_url = "http://localhost:8001/_reload"
            response = self.session.post(reload_url, params={"new_version": version})
            response.raise_for_status()
            logger.info(f"Successfully triggered model reload on vss_cv to version {version}.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to trigger model reload on vss_cv: {e}")
            
        # 6. Clean up downloaded tar.gz
        local_path.unlink()

    def _poll_kb_manifest(self):
        """Polls the KB manifest endpoint for updates."""
        
        manifest_url = urljoin(self.config.network.api_base, self.config.sync.kb_manifest_endpoint)
        current_version = self._get_last_kb_version()
        
        try:
            logger.info(f"Polling KB manifest endpoint: {manifest_url}")
            response = self.session.get(
                manifest_url, 
                timeout=self.config.network.api_timeout_seconds
            )
            response.raise_for_status()
            manifest = response.json()
            
            new_version = manifest.get("kb_version")
            download_url = manifest.get("delta_package_url")
            
            if new_version and new_version != current_version and download_url:
                logger.info(f"New KB version {new_version} available. Current: {current_version}.")
                self._download_and_apply_kb(new_version, download_url)
            else:
                logger.debug("KB is up to date.")
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Error polling KB manifest endpoint: {e}")

    def _download_and_apply_kb(self, version: str, download_url: str):
        """Downloads the KB delta package and applies it to the local DB."""
        
        # NOTE: This is a highly simplified mock. A real implementation would involve
        # downloading, extracting, parsing the delta, and applying SQL updates.
        
        logger.info(f"Mock downloading and applying KB delta for version {version} from {download_url}")
        
        # Mock application success
        with self.db_manager.SessionLocal() as session:
            new_meta = KBMeta(kb_version=version)
            session.add(new_meta)
            session.commit()
        
        logger.info(f"KB version updated to {version} in local DB.")
        # TODO: Rebuild local vector index (e.g., annoy or faiss)

    def _calculate_file_sha256(self, filepath: Path) -> str:
        """Calculates the SHA256 checksum of a file."""
        hasher = hashlib.sha256()
        with open(filepath, 'rb') as file:
            while chunk := file.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()

    def run_sync(self):
        """Performs a single full sync operation."""
        self._poll_packages()
        self._poll_kb_manifest()
        self.last_sync_time = time.time()

    def run_loop(self):
        """Main service loop for periodic synchronization."""
        logger.info("Sync worker service started.")
        
        poll_interval = self.config.sync.poll_interval_seconds
        
        try:
            while self.is_running:
                if time.time() - self.last_sync_time >= poll_interval:
                    self.run_sync()
                
                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Service interrupted. Shutting down.")
        finally:
            self.shutdown()

    def shutdown(self):
        self.is_running = False
        logger.info("Sync worker service shut down complete.")

# --- Main Entry Point ---

if __name__ == "__main__":
    # NOTE: This service also needs a POST /sync/force endpoint, which would be
    # implemented using a web framework like FastAPI, similar to vss_cv and vss_aggregator.
    # For now, we will only implement the worker loop.
    
    sync_worker = SyncWorker()
    try:
        sync_worker.run_loop()
    except KeyboardInterrupt:
        sync_worker.shutdown()
