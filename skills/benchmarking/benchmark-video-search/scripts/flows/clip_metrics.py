# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Raw clip AP@k, independent of retrieval and segment-overlap evaluation."""


def gallery_sensors(clips):
    result = {}
    identities = set()
    for clip in clips:
        cid, sid = clip["dataset_id"], clip["sensor_id"]
        if not cid or not sid or sid in result or cid in identities:
            raise ValueError("Missing or duplicate gallery identity")
        result[sid] = cid
        identities.add(cid)
    if not result:
        raise ValueError("Empty gallery")
    return result


def score_query(query, hits, sensors, k=10):
    if type(k) is not int or k < 1:
        raise ValueError("k must be a positive integer")
    relevant = set(query["relevant_clip_ids"])
    if (
        not relevant
        or len(relevant) != len(query["relevant_clip_ids"])
        or not relevant <= set(sensors.values())
    ):
        raise ValueError("Invalid relevant gallery clips")
    ranking = []
    for hit in hits:
        cid = sensors.get(hit.get("sensor_id"))
        if cid is None:
            raise ValueError("Unknown sensor identity")
        if hit.get("clip_id") is not None and hit["clip_id"] != cid:
            raise ValueError("Conflicting clip identity")
        if cid not in ranking:
            ranking.append(cid)
    found = 0
    contributions = []
    for rank, cid in enumerate(ranking[:k], 1):
        if cid in relevant:
            found += 1
            contributions.append(found / rank)
    numerator = sum(contributions)
    return {
        "query_id": query["query_id"],
        "domain": query["query_domain"],
        f"ap_at_{k}": numerator / min(len(relevant), k),
        f"legacy_ap_at_{k}_all_R": numerator / len(relevant),
        "ranking": ranking,
        "raw_hits": len(hits),
        "unique_clips": len(ranking),
        "relevant_count": len(relevant),
    }


def summarize(queries, captures, sensors, k=10):
    if not queries:
        raise ValueError("Empty query cohort")
    expected = {q["query_id"] for q in queries}
    if len(expected) != len(queries) or set(captures) != expected:
        raise ValueError("Incomplete, duplicate or foreign query captures")
    rows = []
    for q in queries:
        result = captures[q["query_id"]]
        if result.get("exit_code") != 0 or not isinstance(result.get("data"), list):
            raise ValueError("Failed or malformed capture cannot be scored")
        rows.append(score_query(q, result["data"], sensors, k=k))
    summary = {}
    for domain in ("event", "pas", "overall"):
        subset = [r for r in rows if domain == "overall" or r["domain"] == domain]
        summary[domain] = {
            "queries": len(subset),
            f"mAP@{k}_percent": 100 * sum(r[f"ap_at_{k}"] for r in subset) / len(subset)
            if subset
            else None,
            f"legacy_mAP@{k}_percent": 100
            * sum(r[f"legacy_ap_at_{k}_all_R"] for r in subset)
            / len(subset)
            if subset
            else None,
        }
    return {"scorer": "raw-clips-v2-minimal", "rows": rows, "summary": summary}
