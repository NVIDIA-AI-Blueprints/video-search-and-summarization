import os
import sys
import time
import json
import logging
import ssl
import paho.mqtt.client as mqtt
from pathlib import Path
from typing import Dict, Any, Optional

# Add project root to path for imports
sys.path.append(str(Path(__file__).parent.parent.parent))

from edge_node.config_loader import load_edge_config, EdgeConfig

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("vss_mqtt")

# --- Global Setup ---

PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
SCHEMA_PATH = PROJECT_ROOT / "config" / "schema.json"

config: Optional[EdgeConfig] = None

def load_config():
    """Loads configuration."""
    global config
    if config is None:
        try:
            config = load_edge_config(CONFIG_PATH, SCHEMA_PATH)
            logger.info("Configuration loaded successfully.")
        except Exception as e:
            logger.critical(f"Failed to load configuration: {e}")
            sys.exit(1)

# --- MQTT Client Logic ---

class MQTTClient:
    
    def __init__(self):
        load_config()
        self.config = config
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.config.device.device_id)
        self.is_running = True
        
        # Topics
        self.heartbeat_topic = f"vss/heartbeat/{self.config.device.device_id}"
        self.control_topic = f"vss/control/{self.config.device.device_id}"
        
        # Callbacks
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logger.info("Connected to MQTT broker successfully.")
            # Subscribe to control topic
            client.subscribe(self.control_topic)
            logger.info(f"Subscribed to control topic: {self.control_topic}")
        else:
            logger.error(f"Failed to connect to MQTT broker, return code {reason_code}")

    def _on_message(self, client, userdata, msg):
        logger.info(f"Received message on topic {msg.topic}: {msg.payload.decode()}")
        
        try:
            message = json.loads(msg.payload.decode())
            
            if msg.topic == self.control_topic:
                self._handle_control_message(message)
                
        except json.JSONDecodeError:
            logger.error("Received non-JSON message on control topic.")
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def _handle_control_message(self, message: Dict[str, Any]):
        """Handles messages received on the control topic."""
        action = message.get("action")
        
        if action == "request_clip":
            camera_id = message.get("camera_id")
            from_ts = message.get("from")
            to_ts = message.get("to")
            request_id = message.get("request_id")
            
            if not all([camera_id, from_ts, to_ts, request_id]):
                logger.error("Invalid 'request_clip' message format.")
                return
            
            logger.info(f"Received clip request for {camera_id} from {from_ts} to {to_ts}. Request ID: {request_id}")
            
            # TODO: Integrate with vss_ingest's clip extraction logic
            # For now, we'll just log the action.
            # In a real system, this would call a local API endpoint on vss_ingest
            # to trigger the clip extraction and then enqueue the resulting file
            # for upload via vss_aggregator/vss_uploader.
            
            logger.warning("Clip extraction logic is a placeholder. Needs integration with vss_ingest and vss_aggregator.")
            
        else:
            logger.warning(f"Unknown control action received: {action}")

    def connect(self):
        """Configures and connects the MQTT client."""
        
        if self.config.network.mqtt_tls:
            cert_paths = self.config.network.cert_paths
            
            # Configure TLS/SSL
            self.client.tls_set(
                ca_certs=cert_paths.ca_cert,
                certfile=cert_paths.client_cert,
                keyfile=cert_paths.client_key,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLSv1_2
            )
            logger.info("MQTT client configured with TLS/mTLS.")

        try:
            self.client.connect(
                self.config.network.mqtt_broker, 
                self.config.network.mqtt_port, 
                keepalive=60
            )
        except Exception as e:
            logger.error(f"Could not connect to MQTT broker: {e}")
            self.is_running = False

    def publish_event(self, camera_id: str, event_data: Dict[str, Any]):
        """Publishes an event to the specific camera topic."""
        topic = f"{self.config.network.mqtt_topic_prefix}/{self.config.device.tenant_id}/{camera_id}"
        payload = json.dumps(event_data)
        self.client.publish(topic, payload)
        logger.debug(f"Published event to {topic}")

    def publish_heartbeat(self):
        """Publishes a periodic heartbeat message."""
        
        # TODO: Get actual device_version, uptime, free_disk_percent, gpu_temp
        heartbeat_payload = {
            "device_id": self.config.device.device_id,
            "device_version": "v1.0.0", # Placeholder
            "uptime": time.time() - self.start_time,
            "free_disk_percent": 50.0, # Placeholder
            "gpu_temp": 45.0 # Placeholder
        }
        
        self.client.publish(self.heartbeat_topic, json.dumps(heartbeat_payload))
        logger.debug(f"Published heartbeat to {self.heartbeat_topic}")

    def run_loop(self):
        """Main service loop."""
        self.start_time = time.time()
        self.connect()
        
        if not self.is_running:
            return

        # Start the network loop in a non-blocking way
        self.client.loop_start()
        
        HEARTBEAT_INTERVAL = 60 # seconds
        last_heartbeat = 0
        
        try:
            while self.is_running:
                if time.time() - last_heartbeat > HEARTBEAT_INTERVAL:
                    self.publish_heartbeat()
                    last_heartbeat = time.time()
                
                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Service interrupted. Shutting down.")
        finally:
            self.shutdown()

    def shutdown(self):
        self.is_running = False
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("MQTT service shut down complete.")

# --- Main Entry Point ---

if __name__ == "__main__":
    mqtt_service = MQTTClient()
    mqtt_service.run_loop()
