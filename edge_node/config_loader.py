import json
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator
from jsonschema import validate, exceptions

# --- Pydantic Models for Typed Configuration ---

class CameraConfig(BaseModel):
    id: str = Field(..., description="Unique camera ID.")
    index: int = Field(..., description="Camera index in the NVR.")
    label: str = Field(..., description="Human-readable label for the camera.")

class NVRConfig(BaseModel):
    name: str = Field(..., description="Name of the NVR.")
    host: str = Field(..., description="IP address or hostname of the NVR.")
    onvif_port: int = Field(..., description="ONVIF port.")
    username: str = Field(..., description="Username for NVR access.")
    password: str = Field(..., description="Password for NVR access.")
    camera_rtsp_template: str = Field(..., description="RTSP template for direct connection.")
    cameras: List[CameraConfig] = Field(..., description="List of cameras connected to this NVR.")

class DeviceConfig(BaseModel):
    device_id: str = Field(..., description="Unique identifier for the edge device.")
    tenant_id: str = Field(..., description="Tenant or customer ID.")
    location: str = Field(..., description="Physical location of the device.")
    keep_local_days: int = Field(..., description="Number of days to keep local clips before deletion.")
    max_disk_usage_percent: int = Field(..., description="Maximum disk usage percentage before stopping ingestion.")

class NetworkCertPaths(BaseModel):
    client_cert: str
    client_key: str
    ca_cert: str

class NetworkConfig(BaseModel):
    mqtt_broker: str
    mqtt_port: int
    mqtt_tls: bool
    mqtt_topic_prefix: str
    api_base: str
    api_timeout_seconds: int
    use_mtls: bool
    cert_paths: NetworkCertPaths

class IngestConfig(BaseModel):
    chunk_seconds: int
    max_local_clips: int

class UploadConfig(BaseModel):
    presigned_endpoint: str
    metadata_endpoint: str
    upload_complete_endpoint: str
    max_retries: int
    retry_backoff_seconds: int

class SyncConfig(BaseModel):
    packages_endpoint: str
    kb_manifest_endpoint: str
    poll_interval_seconds: int

class EdgeConfig(BaseModel):
    """The main typed configuration for the Edge Node."""
    device: DeviceConfig
    network: NetworkConfig
    nvr_list: List[NVRConfig]
    ingest: IngestConfig
    upload: UploadConfig
    sync: SyncConfig

    @field_validator('nvr_list')
    @classmethod
    def check_unique_camera_ids(cls, v: List[NVRConfig]) -> List[NVRConfig]:
        """Ensure all camera IDs across all NVRs are unique."""
        all_camera_ids = set()
        for nvr in v:
            for camera in nvr.cameras:
                if camera.id in all_camera_ids:
                    raise ValueError(f"Duplicate camera ID found: {camera.id}")
                all_camera_ids.add(camera.id)
        return v

# --- Configuration Loader and Validator ---

def load_config_data(config_path: Path) -> Dict[str, Any]:
    """Loads configuration data from a YAML file."""
    try:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Configuration file not found at: {config_path}")
    except yaml.YAMLError as e:
        raise ValueError(f"Error parsing YAML file: {e}")

def load_schema(schema_path: Path) -> Dict[str, Any]:
    """Loads the JSON schema from a file."""
    try:
        with open(schema_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"Schema file not found at: {schema_path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Error parsing JSON schema file: {e}")

def validate_config_with_schema(config_data: Dict[str, Any], schema: Dict[str, Any]):
    """Validates configuration data against the JSON schema."""
    try:
        validate(instance=config_data, schema=schema)
    except exceptions.ValidationError as e:
        # Provide a more readable error message
        path = ".".join(map(str, e.path))
        raise exceptions.ValidationError(f"Configuration validation failed at '{path}': {e.message}")

def load_edge_config(config_path: Path, schema_path: Path) -> EdgeConfig:
    """
    Loads, validates, and returns the typed EdgeConfig object.
    """
    config_data = load_config_data(config_path)
    schema = load_schema(schema_path)

    # 1. Validate against JSON Schema for basic structure and types
    validate_config_with_schema(config_data, schema)

    # 2. Validate against Pydantic models for complex logic (e.g., unique IDs)
    try:
        return EdgeConfig(**config_data)
    except ValidationError as e:
        # Pydantic errors are already detailed, but we can format them for the CLI
        error_messages = []
        for error in e.errors():
            loc = ".".join(map(str, error['loc']))
            error_messages.append(f"Field '{loc}': {error['msg']}")
        raise ValueError("Pydantic validation failed:\n" + "\n".join(error_messages))

# --- CLI Implementation ---

def cli_validate(config_file: str):
    """
    CLI command to validate a configuration file.
    """
    try:
        config_path = Path(config_file)
        # Assuming schema.json is in the same directory as config.yaml for the CLI
        # For production use, the schema path should be absolute or relative to the script
        schema_path = Path(__file__).parent.parent / "config" / "schema.json"
        
        # Adjust schema_path if the config file is passed from a different location
        if not schema_path.exists():
             schema_path = config_path.parent / "schema.json"
             if not schema_path.exists():
                 # Fallback to the expected location in the repo root
                 schema_path = Path(__file__).parent.parent.parent / "config" / "schema.json"
        
        if not schema_path.exists():
             print(f"Error: Could not find schema.json. Looked in: {Path(__file__).parent.parent / 'config' / 'schema.json'}, {config_path.parent / 'schema.json'}, and {Path(__file__).parent.parent.parent / 'config' / 'schema.json'}", file=sys.stderr)
             sys.exit(1)

        load_edge_config(config_path, schema_path)
        print(f"Configuration file '{config_file}' is valid.")
        sys.exit(0)
    except (FileNotFoundError, ValueError, exceptions.ValidationError) as e:
        print(f"Configuration validation failed for '{config_file}':\n{e}", file=sys.stderr)
        sys.exit(1)

def main():
    """
    Main entry point for the CLI.
    Usage: python -m edge_node.config_loader validate /path/to/config.yaml
    """
    if len(sys.argv) < 3 or sys.argv[1] != "validate":
        print("Usage: python -m edge_node.config_loader validate /path/to/config.yaml", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    config_file = sys.argv[2]

    if command == "validate":
        cli_validate(config_file)
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    # Add __init__.py to make it a package
    Path(__file__).parent.joinpath("__init__.py").touch()
    main()
