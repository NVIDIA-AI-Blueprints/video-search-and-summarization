# SPDX-License-Identifier: Apache-2.0
"""Materialize frame-supported PAS clip GT from an extracted PhysicalAI release.

Annotations are used only by evaluation, never by native indexing or retrieval.
The release's directional substring matching convention is preserved.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ATTRIBUTE_KEYS = {
    "top_outer_type": "top outer type",
    "top_outer_color": "top outer color",
    "bottom_type": "bottom type",
    "bottom_color": "bottom color",
    "shoe_type": "shoe type",
    "shoe_color": "shoe color",
    "viewpoint": "viewpoint",
    "action": "action",
    "accessories": "accessories",
}
EMPTY = {"", "unknown", "none", "not visible", "no", "other", "nothing", "n/a"}


def supports(query, attributes):
    def norm(value):
        return re.sub(r"\s+", " ", str(value).strip().lower())

    for field, required in query["resolved_attributes"].items():
        actual = attributes[ATTRIBUTE_KEYS[re.sub(r"\[\d+\]$", "", field)]]
        actual = actual if isinstance(actual, list) else [actual]
        required = required if isinstance(required, list) else [required]
        if not all(
            any(
                norm(e) not in EMPTY and norm(a) not in EMPTY and norm(e) in norm(a)
                for a in actual
            )
            for e in required
        ):
            return False
    return True


def build(dataset: Path, output: Path, frozen: Path | None = None):
    dataset = dataset.resolve(strict=True)
    tracks = json.loads(
        (dataset / "additional_gt/tracks_pas_attributes.json").read_text()
    )["tracks"]
    queries = json.loads((dataset / "gt/queries_gt.json").read_text())["queries"]
    by_track = {t["track_uid"]: t for t in tracks}
    if len(by_track) != len(tracks):
        raise ValueError("Duplicate track IDs")
    eligible = {u for u, t in by_track.items() if t["eligible_for_gt"]}
    valid, unsupported = [], []
    for query in sorted(
        (q for q in queries if q["query_domain"] == "pas"), key=lambda q: q["query_id"]
    ):
        released = query["relevant_track_uids"]
        if len(set(released)) != len(released) or not set(released).issubset(eligible):
            raise ValueError("Invalid released positive tracks: " + query["query_id"])
        positive = [
            u
            for u in released
            if supports(query, by_track[u]["representative_frame"]["frame_attribute"])
        ]
        row = {
            **query,
            "frame_supported_track_uids": positive,
            "relevant_clip_ids": sorted({by_track[u]["chunk_id"] for u in positive}),
        }
        (valid if positive else unsupported).append(row)
    if frozen is not None:
        expected = {
            q["query_id"]: q for q in map(json.loads, frozen.read_text().splitlines())
        }
        if set(expected) != {q["query_id"] for q in valid}:
            raise ValueError("Frozen query cohort differs")
        for row in valid:
            other = expected[row["query_id"]]
            if (
                row["query"] != other["query"]
                or row["resolved_attributes"] != other["resolved_attributes"]
                or set(row["frame_supported_track_uids"])
                != set(other["frame_supported_track_uids"])
            ):
                raise ValueError("Frozen GT differs: " + row["query_id"])
    output.mkdir(parents=True, exist_ok=False)
    (output / "gt").mkdir()
    (output / "additional_gt").mkdir()
    (output / "clips").symlink_to(dataset / "clips", target_is_directory=True)
    (output / "additional_gt/clips_gt.json").write_bytes(
        (dataset / "additional_gt/clips_gt.json").read_bytes()
    )
    (output / "gt/queries_gt.json").write_text(
        json.dumps({"queries": valid}, indent=2) + "\n"
    )
    for name, rows in [
        ("frame_supported_ground_truth", valid),
        ("unsupported_queries", unsupported),
    ]:
        (output / (name + ".jsonl")).write_text(
            "".join(json.dumps(row) + "\n" for row in rows)
        )
    report = {
        "valid_queries": len(valid),
        "unsupported_queries": len(unsupported),
        "eligible_tracks": len(eligible),
        "positive_query_clip_pairs": sum(len(q["relevant_clip_ids"]) for q in valid),
    }
    (output / "protocol.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--ground-truth",
        type=Path,
        help="Optional frozen JSONL for exact parity verification",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.dataset_root, args.output_dir, args.ground_truth), indent=2
        )
    )
