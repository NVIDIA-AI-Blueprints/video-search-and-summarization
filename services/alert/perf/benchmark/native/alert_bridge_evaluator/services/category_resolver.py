"""
Category / alert_type resolution for Alert Bridge requests.

The evaluator and Alert Bridge agree on a string ``alert_type`` (= ``category``
in the /ondemand payload) that AB uses as the lookup key into Redis (or its
seed file ``alert_type_config.json``) for the prompt + VLM params.

Constraints from AB (see ``alert-agent-web/app/api/alert_config_schemas.py``):

* ``alert_type`` is ``[A-Za-z0-9_\\- ]+`` (alphanumeric, space, underscore,
  hyphen). Anything else (``/``, ``.``, ``:``) is rejected with HTTP 422.
* AB normalizes by ``str.lower().strip()`` (see ``handlers/alert_config/
  normalize.py``).
* The /ondemand endpoint applies the same normalization but does NOT enforce
  the alphanumeric rule, so we keep the slug rule on the evaluator side to
  guarantee parity between ``POST /verification/config`` (seeding) and
  ``POST /verification/ondemand`` (submitting).

Slug shape:

    "traffic_chunks/1PMF46_BJD2PAT5Q4_2" -> "traffic_chunks_1pmf46_bjd2pat5q4_2"

Anything outside the allowed set is replaced with ``_``. The result is always
lowercased to match AB's normalization. Empty / None input raises ValueError
because that is a programming error, not a runtime data condition.

Two resolvers are exported:

* :func:`gt_entry_to_alert_type` — legacy "best-effort id-or-video" picker
  that *cannot* tell duplicate-video rows apart. Kept only for callers that
  already know they're working with id-keyed GT (BCQ / annotation format).
* :func:`gt_row_slug` — the row-stable resolver. Use this anywhere the seed
  pass and the submit pass need to produce the *same* slug for the *same*
  GT row, even when multiple rows share a ``video`` field. This is what
  fixes the "duplicate video, wrong prompt" bug in the legacy gt.json.
"""

import re
from typing import Any, Dict, Optional

# AB only accepts [A-Za-z0-9 _-]; replace anything else with `_`.
_ALERT_TYPE_INVALID_CHARS = re.compile(r"[^A-Za-z0-9 _\-]")


def slugify_alert_type(raw: str) -> str:
    """Convert an arbitrary string into an AB-compatible ``alert_type``.

    Lowercases and replaces invalid characters (``/``, ``.``, ``:``, …) with
    ``_``. Adjacent invalid runs collapse into a single ``_`` so the slug
    stays compact and stable.

    Raises:
        ValueError: when ``raw`` is None / empty / whitespace-only — those
            states indicate the caller built the slug from a missing GT
            field, which would otherwise produce a useless empty key.
    """
    if not raw or not raw.strip():
        raise ValueError("Cannot slugify empty alert_type source")
    cleaned = _ALERT_TYPE_INVALID_CHARS.sub("_", raw.strip())
    # collapse repeated underscores so paths like "a//b" become "a_b" not "a__b"
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.lower()


def gt_entry_to_alert_type(gt_entry: Dict[str, Any]) -> Optional[str]:
    """Derive the AB ``alert_type`` slug from a GT entry (legacy resolver).

    Looks up keys in this order:

    1. ``id``         — used by BCQ annotation format (e.g. ``test_annotation.json``)
    2. ``video_id``   — used by the legacy BCQ items format
    3. ``video``      — last-resort fallback for the dataframe format

    Returns ``None`` when the entry is None / empty so callers can fall back
    to their own default (e.g. ``video`` argument).

    .. warning::
        This resolver collapses every row that shares ``video`` into the
        same slug, so duplicate-video GTs (legacy ``gt.json`` with multiple
        questions per video) end up with last-write-wins seeding and a
        wrong-prompt mismatch on submit. New code should use
        :func:`gt_row_slug` instead, which keeps duplicate-video rows
        distinct via the row index.
    """
    if not gt_entry:
        return None
    for key in ("id", "video_id", "video"):
        value = gt_entry.get(key)
        if isinstance(value, str) and value.strip():
            return slugify_alert_type(value)
    return None


def gt_row_slug(gt_entry: Optional[Dict[str, Any]], row_index: int) -> str:
    """Stable per-row alert_type slug.

    Use this anywhere the seed pass and the submit pass need to agree on
    the *same* slug for the *same* GT row, even when several rows share a
    ``video`` field. Resolution order:

    1. ``entry["id"]``       — explicit, presumed unique per row.
    2. ``entry["video_id"]`` — fallback for BCQ ``items`` format.
    3. ``f"{slugify(video)}__row{idx}"`` — deterministic synthetic slug
       that keeps duplicate-video rows distinct. The ``__row<idx>``
       suffix is stable as long as GT row order is stable, which is what
       ``EvaluationService.load_ground_truth`` relies on.
    4. ``f"row{idx}"`` — last-resort when neither id nor video is set.

    The function never raises; it always returns a non-empty AB-compatible
    slug. Callers (seed loop and ``_resolve_category``) need that guarantee
    so they don't have to special-case malformed entries on the hot path.

    Args:
        gt_entry: A single GT row dict (may be None / missing fields).
        row_index: 0-based index of the entry within the GT list. Used to
            disambiguate duplicate-video rows. Callers must pass the same
            index in seed and submit (the evaluator stamps it via
            ``_eval_row_idx`` on the entry dict at GT load time).

    Returns:
        AB-compatible ``alert_type`` slug.
    """
    if isinstance(gt_entry, dict):
        for key in ("id", "video_id"):
            value = gt_entry.get(key)
            if isinstance(value, str) and value.strip():
                return slugify_alert_type(value)
        video = gt_entry.get("video")
        if isinstance(video, str) and video.strip():
            return f"{slugify_alert_type(video)}__row{row_index}"
    return f"row{row_index}"
