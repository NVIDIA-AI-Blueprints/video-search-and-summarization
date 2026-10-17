# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""PhysicalAI v1.1.2 representative-frame PAS relevance, independent of retrieval.

Ported from the eval-replication bundle as a pure, dependency-free ground-truth
derivation: it rewrites a PAS query's ``relevant_clip_ids`` from the *released*
positive tracks whose representative frame actually supports the query's
resolved attributes, and lists PAS queries that end up with no supported
positive. Event-domain queries are passed through unchanged.

It is a scoring-input transform, not a scorer: ``gallery.build_context`` applies
it when ``--pas-frame-supported`` is set, before the clip-relevance scorer sees
the queries. It deliberately raises ``ValueError`` (not ``SystemExit``) so the
logic stays unit-testable; the gallery loader translates that to a loud exit.
"""

from __future__ import annotations

import re

#: Bumped when the derivation protocol changes; recorded in the score summary.
VERSION = "physicalai-pas-frame-supported-v1"

#: Attribute values that cannot support a requirement, however it is phrased.
UNKNOWN = {"", "unknown", "none", "not visible", "no", "other", "nothing", "n/a"}

#: The PAS attribute vocabulary a requirement may name (sans any ``[n]`` suffix).
FIELDS = {
    "top_outer_type",
    "top_outer_color",
    "bottom_type",
    "bottom_color",
    "shoe_type",
    "shoe_color",
    "viewpoint",
    "action",
    "accessories",
}


def supports(requirements, annotations):
    """True iff every required attribute value is matched by an annotation value.

    Matching is directional and substring-based (a required ``"white"`` is
    supported by an annotated ``"white shoes"``), after normalising whitespace
    and lower-casing. A requirement or annotation whose value is in
    :data:`UNKNOWN` can never support anything, so an explicit ``"unknown"`` /
    ``"not visible"`` annotation does not satisfy a requirement that happens to
    contain that word. ``requirements`` may use a ``field[n]`` suffix to target a
    specific item of a list attribute; the suffix is stripped before lookup.
    """

    def values(value):
        items = value if isinstance(value, list) else [value]
        return [re.sub(r"\s+", " ", str(v).strip().lower()) for v in items]

    for field, required in requirements.items():
        key = re.sub(r"\[\d+\]$", "", field)
        if key not in FIELDS:
            raise ValueError(f"Unsupported PAS attribute: {field}")
        actual = values(annotations[key.replace("_", " ")])
        if not all(
            want not in UNKNOWN
            and any(
                candidate not in UNKNOWN and want in candidate for candidate in actual
            )
            for want in values(required)
        ):
            return False
    return True


def frame_supported_queries(queries, tracks, gallery):
    """Derive frame-supported PAS positives; return ``(kept, excluded)``.

    ``tracks`` is the ``tracks_pas_attributes.json`` payload (a list of track
    records with ``track_uid``, ``chunk_id``, ``eligible_for_gt`` and a
    ``representative_frame.frame_attribute`` dict). ``gallery`` is the set of
    clip IDs the retrieval ran against -- derived positives must land inside it.

    PAS queries are rewritten in place into *copied* dicts: their
    ``relevant_clip_ids``/``n_relevant`` become the supported clips and their
    ``relevant_track_uids``/``n_relevant_tracks`` the supporting tracks; the old
    temporal ``segments`` (which describe the released GT, not this derived one)
    are dropped. A PAS query whose released positives none of which the
    representative frame supports is *excluded* and listed with reason
    ``no_frame_supported_positives``. Event-domain queries are copied through
    untouched. The input ``queries`` list is never mutated.
    """
    by_id = {t["track_uid"]: t for t in tracks}
    if len(by_id) != len(tracks):
        raise ValueError("Duplicate PAS track IDs")
    eligible = {uid for uid, t in by_id.items() if t["eligible_for_gt"]}
    kept, excluded = [], []
    for q in queries:
        if q["query_domain"] != "pas":
            kept.append(dict(q))
            continue
        released = q["relevant_track_uids"]
        if len(released) != len(set(released)) or not set(released) <= eligible:
            raise ValueError(f"Invalid released PAS positive tracks: {q['query_id']}")
        supported = [
            uid
            for uid in released
            if supports(
                q["resolved_attributes"],
                by_id[uid]["representative_frame"]["frame_attribute"],
            )
        ]
        clips = sorted({by_id[uid]["chunk_id"] for uid in supported})
        if not set(clips) <= set(gallery):
            raise ValueError(f"PAS positives outside gallery: {q['query_id']}")
        if not clips:
            excluded.append(
                {
                    "query_id": q["query_id"],
                    "query_domain": "pas",
                    "reason": "no_frame_supported_positives",
                }
            )
            continue
        updated = {
            **q,
            "relevant_clip_ids": clips,
            "n_relevant": len(clips),
            "relevant_track_uids": supported,
            "n_relevant_tracks": len(supported),
        }
        # Original temporal segments describe the released GT, not this derived GT.
        updated.pop("segments", None)
        kept.append(updated)
    return kept, excluded
