"""Utilities for generating synthetic PASS crops on PCB board images.

A PASS crop is a randomly placed bounding box that:
  - lies entirely within the image bounds
  - does not overlap any excluded region (e.g. annotated defect bboxes)
  - does not overlap previously placed PASS crops in the same image

All bounding boxes use the (x_min, y_min, x_max, y_max) convention.
"""

from __future__ import annotations

import random

BBox = tuple[int, int, int, int]  # (x_min, y_min, x_max, y_max)


def boxes_overlap(a: BBox, b: BBox) -> bool:
    """Return True when two boxes share any area."""
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def generate_pass_crops(
    img_width: int,
    img_height: int,
    excluded_bboxes: list[BBox],
    n_crops: int,
    rng: random.Random,
    min_crop_w: int = 32,
    min_crop_h: int = 32,
    max_crop_w: int = 128,
    max_crop_h: int = 128,
    max_attempts: int = 200,
) -> list[BBox]:
    """Return up to *n_crops* non-overlapping PASS crop boxes.

    Each crop gets a independently random size drawn from
    [min_crop_w, max_crop_w] x [min_crop_h, max_crop_h], then a random
    position within the image. Boxes must not overlap any entry in
    *excluded_bboxes* or each other. Fewer than *n_crops* boxes are returned
    when placement consistently fails after *max_attempts* per crop.
    """
    placed: list[BBox] = []
    for _ in range(n_crops):
        crop_w = rng.randint(min_crop_w, max_crop_w)
        crop_h = rng.randint(min_crop_h, max_crop_h)
        if img_width < crop_w or img_height < crop_h:
            continue
        for _ in range(max_attempts):
            left = rng.randint(0, img_width - crop_w)
            top = rng.randint(0, img_height - crop_h)
            box: BBox = (left, top, left + crop_w, top + crop_h)
            if not any(boxes_overlap(box, ex) for ex in excluded_bboxes + placed):
                placed.append(box)
                break
    return placed
