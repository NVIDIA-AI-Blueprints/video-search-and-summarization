# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Open Graph preview card for a published view.

Telegram and WhatsApp cannot render HTML -- a shared link becomes a preview
card built from OG tags plus one image. This renders that image.

Deliberately PIL and not a headless browser: the card is a title, a count, and
a strip of thumbnails. A faithful page render would roughly double the image
size and add a browser to the attack surface for a 1200x630 PNG.

Pure functions over bytes, so the caller owns all network I/O and this stays
unit-testable without Redis or VST.
"""

from __future__ import annotations

from io import BytesIO
import logging

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont

logger = logging.getLogger(__name__)

# Open Graph's recommended card size. Telegram and WhatsApp both crop toward
# the centre, so keep meaningful content away from the edges.
CARD_W = 1200
CARD_H = 630

_BG = (17, 20, 24)
_FG = (243, 244, 246)
_MUTED = (156, 163, 175)
_ACCENT = (118, 185, 0)  # NVIDIA green
_TILE_BG = (31, 36, 43)

_PAD = 56
_TILE_GAP = 16
_TILE_COUNT = 4


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Best-effort font lookup.

    Distroless images carry no fonts, so the bitmap default is the expected
    path in production rather than an error case. The card degrades to smaller
    text instead of failing to render.
    """
    candidates = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold
        else ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _truncate(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]
    return text + ellipsis


def _tile(raw: bytes, box_w: int, box_h: int) -> Image.Image | None:
    """Decode one thumbnail and cover-crop it to the tile box."""
    try:
        img = Image.open(BytesIO(raw))
        img.load()
        img = img.convert("RGB")
    except Exception as exc:
        logger.warning("preview: dropping undecodable thumbnail: %s", exc)
        return None

    src_w, src_h = img.size
    if src_w <= 0 or src_h <= 0:
        return None

    scale = max(box_w / src_w, box_h / src_h)
    resized = img.resize((max(1, round(src_w * scale)), max(1, round(src_h * scale))), Image.LANCZOS)
    left = (resized.width - box_w) // 2
    top = (resized.height - box_h) // 2
    return resized.crop((left, top, left + box_w, top + box_h))


def render_card(
    *,
    title: str,
    subtitle: str,
    footer: str,
    thumbnails: list[bytes],
) -> bytes:
    """Render the preview card to PNG bytes.

    Args:
        title: Headline, usually the query text.
        subtitle: One line of context, e.g. "8 results - camera-3, camera-7".
        footer: Provenance line, e.g. "VSS search - expires in 7 days".
        thumbnails: Raw image bytes, in result order. Undecodable entries are
            skipped; an empty list renders a valid card with placeholder tiles.

    Returns:
        PNG bytes, always. Never raises for content reasons -- a shared link
        must still preview even when every thumbnail is unavailable.
    """
    card = Image.new("RGB", (CARD_W, CARD_H), _BG)
    draw = ImageDraw.Draw(card)

    draw.rectangle([(0, 0), (CARD_W, 8)], fill=_ACCENT)

    title_font = _font(52, bold=True)
    subtitle_font = _font(30)
    footer_font = _font(24)

    text_w = CARD_W - 2 * _PAD
    y = _PAD + 8

    draw.text((_PAD, y), _truncate(draw, title, title_font, text_w), font=title_font, fill=_FG)
    y += 74

    draw.text((_PAD, y), _truncate(draw, subtitle, subtitle_font, text_w), font=subtitle_font, fill=_MUTED)
    y += 62

    # Thumbnail strip fills the remaining vertical space above the footer.
    strip_top = y
    strip_bottom = CARD_H - _PAD - 36
    tile_h = max(80, strip_bottom - strip_top)
    tile_w = (text_w - _TILE_GAP * (_TILE_COUNT - 1)) // _TILE_COUNT

    usable = [t for t in (_tile(raw, tile_w, tile_h) for raw in thumbnails[:_TILE_COUNT]) if t is not None]

    for index in range(_TILE_COUNT):
        x = _PAD + index * (tile_w + _TILE_GAP)
        draw.rectangle([(x, strip_top), (x + tile_w, strip_top + tile_h)], fill=_TILE_BG)
        if index < len(usable):
            card.paste(usable[index], (x, strip_top))

    draw.text((_PAD, CARD_H - _PAD - 8), _truncate(draw, footer, footer_font, text_w), font=footer_font, fill=_MUTED)

    out = BytesIO()
    card.save(out, format="PNG", optimize=True)
    return out.getvalue()
