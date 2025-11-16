import os
import sys
import time
import json
import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Local imports
from edge_node.config_loader import load_edge_config, EdgeConfig

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("vss_watchdog")

# --- Global Setup ---

PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
SCHEMA_PATH = PROJECT_ROOT / "config" / "schema.json"

config: Optional[EdgeConfig] = None

# Service names and their expected local ports (placeholders)
SERVICE_PORTS = {
    "vss_ingest": 8000, # Placeholder, vss_ingest needs an HTTP server
    "vss_cv": 8001,
    "vss_aggregator": 8002,
    # vss_uploader, vss_mqtt, vss_sync, vss_watchdog typically don't need a port for health checks
    # but we'll use the watchdog's own port for its health check.
}

def load_config():
    """Loads configuration."""
    global config
    if config is None:
        try:
            config = load_edge_config(CONFIG_PATH, SCHEMA_PATH)
            logger.info("Configuration loaded successfully.")
        except Exception as e:
            logger.critical(f"Failed to load configuration: {e}")
            raise RuntimeError("Configuration failed to load.") from e

# --- Watchdog Logic ---

class Watchdog:
    
    def __init__(self):
        load_config()
        self.config = config
        self.is_running = True
        self.service_statuses: Dict[str, Dict[str, Any]] = {}

    def check_service_health(self, service_name: str, port: int) -> Dict[str, Any]:
        """Checks the health endpoint of a local service."""
        url = f"http://localhost:{port}/health"
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            health_data = response.json()
            health_data["status"] = "OK"
            return health_data
        except requests.exceptions.RequestException as e:
            return {"status": "CRITICAL", "error": str(e)}

    def aggregate_health(self):
        """Aggregates health from all local services."""
        
        # Check HTTP-exposed services
        for name, port in SERVICE_PORTS.items():
            self.service_statuses[name] = self.check_service_health(name, port)
            
        # Check non-HTTP services (e.g., vss_uploader, vss_mqtt, vss_sync)
        # In a real system, these would expose a local file or a shared memory segment
        # for the watchdog to read, or the watchdog would use Docker/systemd APIs.
        
        # For this implementation, we will mock the status of non-HTTP services
        # and focus on the auto-restart hook logic.
        
        if "vss_uploader" not in self.service_statuses:
            self.service_statuses["vss_uploader"] = {"status": "OK", "info": "Mocked status"}
        if "vss_mqtt" not in self.service_statuses:
            self.service_statuses["vss_mqtt"] = {"status": "OK", "info": "Mocked status"}
        if "vss_sync" not in self.service_statuses:
            self.service_statuses["vss_sync"] = {"status": "OK", "info": "Mocked status"}

    def check_and_restart(self):
        """Checks for critical failures and triggers restart hooks."""
        
        for name, status in self.service_statuses.items():
            if status.get("status") == "CRITICAL":
                logger.critical(f"Service {name} is CRITICAL. Attempting restart.")
                self._trigger_restart(name)

    def _trigger_restart(self, service_name: str):
        """
        Triggers a restart of the failed service.
        In a production environment, this would use the Docker API or systemd.
        """
        logger.warning(f"Restart hook for {service_name} triggered. (MOCK: Requires Docker/systemd integration)")
        # Example of a shell command to restart a systemd service:
        # subprocess.run(["sudo", "systemctl", "restart", f"vss-{service_name}.service"])
        
        # For now, we'll just log the action.
        pass

    def run_loop(self):
        """Main service loop for monitoring."""
        logger.info("Watchdog service started.")
        
        try:
            while self.is_running:
                self.aggregate_health()
                self.check_and_restart()
                time.sleep(10) # Check every 10 seconds
                
        except KeyboardInterrupt:
            logger.info("Service interrupted. Shutting down.")
        finally:
            self.shutdown()

    def shutdown(self):
        self.is_running = False
        logger.info("Watchdog service shut down complete.")

# --- FastAPI Application (for /health endpoint) ---

app = FastAPI(
    title="VSS Watchdog Service",
    on_startup=[load_config]
)

watchdog_instance = Watchdog()

@app.get("/health")
async def health_check():
    """Exposes the aggregated health summary."""
    watchdog_instance.aggregate_health()
    
    overall_status = "OK"
    for status in watchdog_instance.service_statuses.values():
        if status.get("status") == "CRITICAL":
            overall_status = "CRITICAL"
            break
            
    return {
        "status": overall_status,
        "services": watchdog_instance.service_statuses
    }

# --- Main Entry Point ---

if __name__ == "__main__":
    import uvicorn
    # The watchdog service runs on port 8003 (arbitrary choice for now)
    uvicorn.run(app, host="0.0.0.0", port=8003)
