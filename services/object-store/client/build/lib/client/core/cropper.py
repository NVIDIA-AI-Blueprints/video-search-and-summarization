"""Cropper: decode the boards once and cut one (test, golden) pair per component.

The test board and the golden-ref board share the same layout, so a single
bounding box cuts the matching region from each.
"""

import base64
import io

from PIL import Image

from client.api.schemas import CropInfoItem
from client.config import settings

# One decoded (cropId, test_png_bytes, golden_png_bytes) tuple.
CropPair = tuple[str, bytes, bytes]


def decode_board(image_b64: str) -> Image.Image:
    """Decode a base64 board image into an RGB PIL image."""
    return Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")


def validate_bounds(
    test_img: Image.Image,
    golden_img: Image.Image,
    crop_infos: list[CropInfoItem],
) -> list[str]:
    """Return a message per crop whose box falls outside either board."""
    errors: list[str] = []
    for item in crop_infos:
        c = item.crop.coordinates
        right, bottom = c.left + c.width, c.top + c.height
        for label, img in (("test", test_img), ("golden", golden_img)):
            if right > img.width or bottom > img.height:
                errors.append(
                    f"crop '{item.id}' out of bounds on {label} board "
                    f"({c.left},{c.top},{c.width},{c.height} vs {img.width}x{img.height})"
                )
                break
    return errors


def _encode_crop(img: Image.Image) -> bytes:
    fmt = settings.crop_format.upper()
    buf = io.BytesIO()
    # PIL requires RGB (no alpha) for JPEG.
    save_img = img.convert("RGB") if fmt == "JPEG" else img
    save_img.save(buf, format=fmt)
    return buf.getvalue()


def crop_all(
    test_img: Image.Image,
    golden_img: Image.Image,
    crop_infos: list[CropInfoItem],
) -> list[CropPair]:
    """Crop one aligned (test, golden) pair per flagged component."""
    pairs: list[CropPair] = []
    for item in crop_infos:
        c = item.crop.coordinates
        box = (c.left, c.top, c.left + c.width, c.top + c.height)
        pairs.append(
            (item.id, _encode_crop(test_img.crop(box)), _encode_crop(golden_img.crop(box)))
        )
    return pairs
