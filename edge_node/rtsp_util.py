import re
from typing import Dict, Any

def format_rtsp_url(template: str, nvr_config: Dict[str, Any], camera_index: int) -> str:
    """
    Formats an RTSP URL using a template and NVR/camera details.

    Args:
        template: The RTSP URL template string.
        nvr_config: The NVR configuration dictionary.
        camera_index: The index of the camera on the NVR.

    Returns:
        The formatted RTSP URL.
    """
    # Use a dictionary of available placeholders
    placeholders = {
        "username": nvr_config.get("username", ""),
        "password": nvr_config.get("password", ""),
        "host": nvr_config.get("host", ""),
        "index": camera_index,
    }

    # Simple string format replacement
    try:
        return template.format(**placeholders)
    except KeyError as e:
        print(f"Warning: RTSP template is missing placeholder: {e}")
        return template
