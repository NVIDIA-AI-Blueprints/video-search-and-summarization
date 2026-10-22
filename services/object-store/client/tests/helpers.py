import base64
import io

from PIL import Image


def _png_b64(width: int = 200, height: int = 200, color=(120, 120, 120)) -> str:
    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


BOARD_B64 = _png_b64(200, 200)

VALID_CROP_INFO = [
    {"id": "U16", "crop": {"coordinates": {"left": 10, "top": 20, "width": 64, "height": 64}}},
]


def make_request(
    crop_info: list | None = None,
    obj_store_path: str = "deft-aoi/3f8a1b2c/photo1.jpg",
    sample: str = BOARD_B64,
    golden: str = BOARD_B64,
) -> dict:
    return {
        "sampleImage": sample,
        "goldenRefImage": golden,
        "metadata": {
            "objStorePath": obj_store_path,
            "imageInfo": {
                "imageType": "full",
                "cropInfo": VALID_CROP_INFO if crop_info is None else crop_info,
            },
        },
    }
