"""Batcher: group crop pairs into batches of at most batch_size."""

from dataclasses import dataclass

from PIL import Image

from client.api.schemas import CropInfoItem
from client.core import cropper
from client.core.cropper import CropPair


@dataclass
class Batch:
    batch_number: int
    crop_ids: list[str]
    test_images: list[bytes]
    golden_images: list[bytes]


def make_batches(pairs: list[CropPair], batch_size: int) -> list[Batch]:
    batches: list[Batch] = []
    for i in range(0, len(pairs), batch_size):
        chunk = pairs[i : i + batch_size]
        batches.append(
            Batch(
                batch_number=i // batch_size,
                crop_ids=[c[0] for c in chunk],
                test_images=[c[1] for c in chunk],
                golden_images=[c[2] for c in chunk],
            )
        )
    return batches


def crop_and_batch(
    test_img: Image.Image,
    golden_img: Image.Image,
    crop_infos: list[CropInfoItem],
    batch_size: int,
) -> list[Batch]:
    """Crop all regions then group into batches — the sole CPU-bound step."""
    pairs = cropper.crop_all(test_img, golden_img, crop_infos)
    return make_batches(pairs, batch_size)
