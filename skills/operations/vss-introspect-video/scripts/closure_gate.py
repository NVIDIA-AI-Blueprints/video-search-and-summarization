#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Requirement-level closure certificates, not a semantic or visual judge."""

from __future__ import annotations

import copy

ANCHORS = {"subject", "event", "state", "reference_frame"}


def exact(value, keys, name):
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{name} requires exactly {sorted(keys)}")


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 400:
        raise ValueError("Closure text must contain 1..400 characters")


def anchors(value):
    exact(value, ANCHORS, "applicability")
    for item in value.values():
        text(item)


def validate_contract(contract, plan):
    ids = {c["claim_id"] for c in plan["claims"]}
    exact(contract, ids, "closure contract")
    for claim_id, item in contract.items():
        exact(item, {"depends_on", "requirements"}, "claim contract")
        deps = item["depends_on"]
        if (
            not isinstance(deps, list)
            or any(not isinstance(d, str) for d in deps)
            or len(deps) != len(set(deps))
            or not set(deps) <= ids
            or claim_id in deps
        ):
            raise ValueError("Dependencies must name distinct other planned claims")
        reqs = item["requirements"]
        if not isinstance(reqs, list) or not 1 <= len(reqs) <= 6:
            raise ValueError("Declare 1..6 closure requirements per claim")
        seen = set()
        for req in reqs:
            exact(req, {"requirement_id", "test", "applicability"}, "requirement")
            text(req["requirement_id"])
            text(req["test"])
            anchors(req["applicability"])
            if req["requirement_id"] in seen:
                raise ValueError("Duplicate closure requirement")
            seen.add(req["requirement_id"])

    def visit(cid, active):
        if cid in active:
            raise ValueError("Cyclic closure dependencies")
        for dependency in contract[cid]["depends_on"]:
            visit(dependency, active | {cid})

    for cid in ids:
        visit(cid, set())


def normalize_certificate(entries, new_observations, current, claim_id):
    """Resolve local observation indices, then retain immutable canonical IDs."""
    if not isinstance(entries, list) or len(entries) > 6:
        raise ValueError("closure_evidence must be an array of at most six entries")
    known = {o["observation_id"]: o for o in current["observations"]}
    known.update({o["observation_id"]: o for o in new_observations})
    allowed = {
        r["requirement_id"]
        for r in current["closure_contract"][claim_id]["requirements"]
    }
    result, seen = [], set()
    for entry in entries:
        exact(
            entry, {"requirement_id", "evidence", "coverage_sufficient"}, "certificate"
        )
        rid = entry["requirement_id"]
        if not isinstance(rid, str) or rid not in allowed or rid in seen:
            raise ValueError("Unknown or duplicate closure requirement")
        seen.add(rid)
        if type(entry["coverage_sufficient"]) is not bool:
            raise ValueError("coverage_sufficient must be boolean")
        if not isinstance(entry["evidence"], list) or len(entry["evidence"]) > 8:
            raise ValueError("A requirement accepts at most eight evidence bindings")
        bindings = []
        for binding in entry["evidence"]:
            if not isinstance(binding, dict):
                raise ValueError("Evidence binding must be an object")
            key = (
                "observation_index"
                if "observation_index" in binding
                else "observation_id"
            )
            exact(binding, {key, "applicability"}, "evidence binding")
            anchors(binding["applicability"])
            if key == "observation_index":
                index = binding[key]
                if type(index) is not int or not 0 <= index < len(new_observations):
                    raise ValueError("Closure observation_index is outside this update")
                oid = new_observations[index]["observation_id"]
            else:
                oid = binding[key]
            if (
                not isinstance(oid, str)
                or oid not in known
                or known[oid]["claim_id"] != claim_id
            ):
                raise ValueError(
                    "Closure citation is missing or belongs to another claim"
                )
            bindings.append(
                {
                    "observation_id": oid,
                    "applicability": copy.deepcopy(binding["applicability"]),
                }
            )
        result.append({**entry, "evidence": bindings})
    return result


def blockers(current):
    contract = current["closure_contract"]
    observations = {o["observation_id"]: o for o in current["observations"]}
    states = {s["claim_id"]: s for s in current["claims"]}
    result = {}
    for cid, item in contract.items():
        entries = {
            e["requirement_id"]: e for e in current["closure_certificates"].get(cid, [])
        }
        reasons = []
        accepted_relations = {
            o["relation"] for o in observations.values() if o["claim_id"] == cid
        }
        if {"supports", "contradicts"} <= accepted_relations:
            reasons.append("Accepted claim evidence remains contradictory")
        for req in item["requirements"]:
            entry = entries.get(req["requirement_id"])
            if not entry or not entry["evidence"]:
                reasons.append(f"{req['requirement_id']}: missing evidence bindings")
                continue
            if not entry["coverage_sufficient"]:
                reasons.append(f"{req['requirement_id']}: required coverage incomplete")
            relations = set()
            for binding in entry["evidence"]:
                obs = observations[binding["observation_id"]]
                relations.add(obs["relation"])
                if binding["applicability"] != req["applicability"]:
                    reasons.append(
                        f"{req['requirement_id']}: subject/event/state/reference-frame mismatch"
                    )
            if not relations & {"supports", "contradicts"}:
                reasons.append(
                    f"{req['requirement_id']}: context alone is not evidence of the requirement"
                )
            if {"supports", "contradicts"} <= relations:
                reasons.append(f"{req['requirement_id']}: conflicting evidence")
        result[cid] = reasons

    # A dependency can itself have dependencies; propagate to a fixed point.
    for _ in contract:
        for cid, item in contract.items():
            for dep in item["depends_on"]:
                state = states[dep]
                if (
                    result[dep]
                    or state["gap"] is not None
                    or state["coverage"] != "sufficient"
                    or state["status"] not in ("supported", "contradicted")
                ):
                    reason = f"Prerequisite {dep} is unresolved"
                    if reason not in result[cid]:
                        result[cid].append(reason)
    return result


def refresh(current):
    if "closure_contract" in current:
        reasons = blockers(current)
        for state in current["claims"]:
            state["closure_blockers"] = reasons[state["claim_id"]]


def validate_state(current):
    validate_contract(current["closure_contract"], current["plan"])
    certs = current["closure_certificates"]
    if not isinstance(certs, dict) or set(certs) != set(current["closure_contract"]):
        raise ValueError("Closure certificates must match planned claims")
    for cid, entries in certs.items():
        normalized = normalize_certificate(entries, [], current, cid)
        if normalized != entries:
            raise ValueError(
                "Stored closure certificates must use canonical observation IDs"
            )
    expected = blockers(current)
    for state in current["claims"]:
        if state.get("closure_blockers") != expected[state["claim_id"]]:
            raise ValueError("Stale or modified closure blockers")
