"""Flatten AOI eval-set records (one per board) into per-target online samples.

Accepts the AOI live-catalogue object (``metadata.cropInfo[].inference``) and the
standardized ``nv-aoi/v1`` package (``inference_result.targets[]``).

An uninterpretable record contributes no samples rather than raising: the caller
reads an empty set as "insufficient data" and holds.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

#: AOI secondary-inference ``classification`` vocabulary.
GOOD = "Good"
NOT_GOOD = "NotGood"

_PASS_TOKENS = frozenset({"pass", "good", "ok", "no_defect", "nodefect"})
_DEFECT_TOKENS = frozenset({"defect", "notgood", "not_good", "ng", "fail", "reject"})


def normalize_label(value: Any) -> str | None:
    """Map a verdict spelling onto ``GOOD`` / ``NOT_GOOD``.

    ``None`` for anything unrecognised, so "classified good" stays distinct from
    "not classified at all".
    """
    if value is None:
        return None
    token = str(value).strip().lower().replace("-", "_")
    if token in _PASS_TOKENS:
        return GOOD
    if token in _DEFECT_TOKENS:
        return NOT_GOOD
    return None


def normalize_defect_type(value: Any) -> str | None:
    """Map a defect *type* onto ``GOOD`` / ``NOT_GOOD``: PASS is good, any other
    non-empty value names a defect.

    Open vocabulary, unlike :func:`normalize_label`. Used for both the AOI
    reference label and the operator's ``confirmed_label``.
    """
    if value is None:
        return None
    token = str(value).strip()
    if not token:
        return None
    return GOOD if token.lower() in _PASS_TOKENS else NOT_GOOD


def ground_truth(sample: dict) -> str | None:
    """Operator confirmation, else the AOI reference label, else ``None``
    (excluded from FAR)."""
    return sample.get("confirmed_label") or sample.get("reference_label")


def records_to_samples(records: Iterable[Any]) -> list[dict]:
    """Flatten ``records`` into per-target samples, dropping targets the
    candidate did not classify."""
    samples: list[dict] = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        try:
            if "ai_classification" in record:
                samples.append(record)  # already a scored sample
            elif "inference_result" in record:
                samples.extend(_from_standardized(record))
            else:
                samples.extend(_from_live_object(record))
        except Exception:  # pragma: no cover - defensive, shape is external
            logger.warning("Skipping uninterpretable eval-set record", exc_info=True)
    return samples


def _from_live_object(record: dict) -> list[dict]:
    """Samples from an AOI live-catalogue object record."""
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return []
    crops = metadata.get("cropInfo")
    if not isinstance(crops, list):
        return []

    out: list[dict] = []
    for crop in crops:
        if not isinstance(crop, dict):
            continue
        inference = crop.get("inference")
        if not isinstance(inference, dict):
            continue  # never score a target the candidate did not classify
        # A failed/partial crop can still carry a verdict from an earlier
        # attempt; scoring it would put a target the candidate never really
        # called into FAR. Absent status stays readable for back-compat.
        status = inference.get("status")
        if status is not None and str(status).strip().lower() != "completed":
            continue

        candidate = inference.get("new_model")
        candidate = candidate if isinstance(candidate, dict) else {}
        # Single-model deployments populate only the top-level verdict/score.
        ai_class = normalize_label(candidate.get("verdict") or inference.get("verdict"))
        if ai_class is None:
            continue

        baseline = inference.get("old_model")
        baseline = baseline if isinstance(baseline, dict) else {}

        defect_info = crop.get("aoiDefectInfo")
        defect_info = defect_info if isinstance(defect_info, dict) else {}

        score = _as_float(candidate.get("score", inference.get("score")))
        out.append(
            {
                "target_id": inference.get("inspectionId") or crop.get("id"),
                "ai_classification": ai_class,
                "ai_score": score,
                "ai_confidence": score,
                "ai_threshold": _as_float(inference.get("threshold")),
                "baseline_classification": normalize_label(baseline.get("verdict")),
                # Open vocabulary, like the reference label: an operator
                # confirms a DEFECT TYPE ("Mouse Bite", "Capacitor" for a
                # missing part), not the token "NotGood". Parsing this with the
                # closed verdict vocabulary silently discarded the confirmation
                # and fell back to the machine's own label.
                "confirmed_label": normalize_defect_type(
                    crop.get("confirmedLabel") or crop.get("confirmed_label")
                ),
                "reference_label": normalize_defect_type(defect_info.get("defectType")),
                "candidate_version": _as_str(candidate.get("version")),
                "baseline_version": _as_str(baseline.get("version")),
            }
        )
    return out


def _from_standardized(record: dict) -> list[dict]:
    """Samples from a standardized ``nv-aoi/v1`` inspection package."""
    result = record.get("inference_result")
    if not isinstance(result, dict):
        return []
    targets = result.get("targets")
    if not isinstance(targets, list):
        return []

    nv_aoi = record.get("nv_aoi")
    nv_aoi = nv_aoi if isinstance(nv_aoi, dict) else {}
    inspection_id = nv_aoi.get("inspectionId") or record.get("inspection_id")
    reference = _reference_labels(nv_aoi)

    out: list[dict] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        ai_class = normalize_label(target.get("classification"))
        if ai_class is None:
            continue
        component = target.get("component_id")
        out.append(
            {
                "target_id": f"{inspection_id}:{component}" if inspection_id else component,
                "ai_classification": ai_class,
                "ai_score": _as_float(target.get("confidence")),
                "ai_confidence": _as_float(target.get("confidence")),
                "ai_threshold": None,
                "baseline_classification": normalize_label(target.get("baseline_classification")),
                "confirmed_label": normalize_defect_type(target.get("confirmed_label")),
                "reference_label": reference.get(component),
                "candidate_version": _as_str(result.get("model_id")),
                "baseline_version": None,
            }
        )
    return out


def _reference_labels(nv_aoi: dict) -> dict[Any, str]:
    """Component id -> reference label, from the nv-aoi crop list."""
    image_info = nv_aoi.get("imageInfo")
    image_info = image_info if isinstance(image_info, dict) else {}
    crops = image_info.get("cropInfo")
    if not isinstance(crops, list):
        return {}
    labels: dict[Any, str] = {}
    for crop in crops:
        if not isinstance(crop, dict):
            continue
        info = crop.get("aoiDefectInfo")
        info = info if isinstance(info, dict) else {}
        label = normalize_defect_type(info.get("defectType"))
        if label is not None and crop.get("id") is not None:
            labels[crop["id"]] = label
    return labels


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _as_str(value: Any) -> str | None:
    return str(value) if value is not None else None
