import logging
from pathlib import Path
from typing import Dict, Any, List

# Import necessary components from config_loader for type hinting
from edge_node.config_loader import NVRConfig, CameraConfig
from edge_node.rtsp_util import format_rtsp_url
# NOTE: Due to sandbox limitations preventing the installation of a full ONVIF library
# (like python-onvif or onvif-py), this implementation provides a mock
# for the ONVIF discovery part and relies on the RTSP template fallback.
# In a real environment, the commented-out structure would be replaced by
# a proper ONVIF client implementation using 'zeep' or 'onvif-py'.

logger = logging.getLogger(__name__)

def discover_and_resolve(nvr_entry: NVRConfig) -> Dict[str, str]:
    """
    Connects to an NVR, attempts ONVIF discovery to get RTSP URLs,
    and falls back to the camera_rtsp_template if ONVIF fails.

    Args:
        nvr_entry: The NVR configuration object.

    Returns:
        A dictionary mapping camera_id to its resolved RTSP URL.
    """
    rtsp_urls: Dict[str, str] = {}
    onvif_success = False

    # --- Start of Mock ONVIF Implementation ---
    # In a real environment, this section would use a library like 'zeep'
    # to connect to the NVR's ONVIF service and fetch the media profiles.
    
    # try:
    #     from onvif import ONVIFCamera
    #     # Create ONVIF client
    #     mycam = ONVIFCamera(
    #         nvr_entry.host, 
    #         nvr_entry.onvif_port, 
    #         nvr_entry.username, 
    #         nvr_entry.password, 
    #         '/etc/onvif/wsdl/' # Path to WSDL files
    #     )
    #     media_service = mycam.create_media_service()
    #     profiles = media_service.GetProfiles()
    #     
    #     # Logic to map ONVIF profiles/streams to configured cameras (complex, often manual)
    #     # For simplicity in this mock, we assume ONVIF fails or is not used.
    #     
    #     # onvif_success = True
    #     # logger.info(f"Successfully connected to NVR {nvr_entry.name} via ONVIF.")
    #     
    # except Exception as e:
    #     logger.warning(f"ONVIF discovery failed for NVR {nvr_entry.name} ({nvr_entry.host}): {e}. Falling back to RTSP template.")
    
    logger.warning(f"ONVIF discovery is currently mocked/disabled for NVR {nvr_entry.name}. Falling back to RTSP template.")
    # --- End of Mock ONVIF Implementation ---

    if not onvif_success:
        # Fallback: Use the camera_rtsp_template
        for camera in nvr_entry.cameras:
            try:
                rtsp_url = format_rtsp_url(
                    nvr_entry.camera_rtsp_template,
                    nvr_entry.model_dump(), # Pass NVR config as dict
                    camera.index
                )
                rtsp_urls[camera.id] = rtsp_url
                logger.info(f"Resolved RTSP for {camera.id}: {rtsp_url}")
            except Exception as e:
                logger.error(f"Failed to format RTSP URL for camera {camera.id}: {e}")

    return rtsp_urls

# Acceptance test hook (as requested in the prompt)
if __name__ == "__main__":
    # This block simulates the acceptance test:
    # python -c "from edge_node.onvif_client import discover_and_resolve; print(discover_and_resolve('path-to-config'))"
    
    # We need to load the config first\n    from pathlib import Path\n    import sys\n    # Add project root to path for imports\n    sys.path.append(str(Path(__file__).parent.parent.parent))
    from edge_node.config_loader import load_edge_config
    
    # Assuming the script is run from the project root
    CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"
    SCHEMA_PATH = Path(__file__).parent.parent / "config" / "schema.json"
    
    try:
        config = load_edge_config(CONFIG_PATH, SCHEMA_PATH)
        
        print("--- Running ONVIF/RTSP Resolution Test ---")
        all_resolved_urls = {}
        for nvr_entry in config.nvr_list:
            resolved_urls = discover_and_resolve(nvr_entry)
            all_resolved_urls.update(resolved_urls)
            
        import pprint
        pprint.pprint(all_resolved_urls)
        
    except Exception as e:
        print(f"Test failed: {e}")
        sys.exit(1)
