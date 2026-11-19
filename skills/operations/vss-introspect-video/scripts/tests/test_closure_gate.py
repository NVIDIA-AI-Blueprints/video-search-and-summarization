# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import copy

import closure_gate as gate
import evidence_ledger as ledger
import inspection_workflow as workflow
import pytest
from closure_fixtures import ANCHORS, contract_for
from test_inspection_workflow import assessment, initialize, spec


def setup_gate(tmp_path, names=("claim-color",), dependency=False):
    path = initialize(tmp_path, names, choice=True)
    plan = workflow.read(path)["plan"]
    contract = contract_for(plan)
    if dependency:
        contract[names[1]]["depends_on"] = [names[0]]
    ledger.atomic_write(
        path,
        ledger.initialize_ledger(plan, requires_choice=True, closure_contract=contract),
    )
    return path


def certificate(**overrides):
    result = {
        "requirement_id": "fact",
        "evidence": [{"observation_index": 0, "applicability": copy.deepcopy(ANCHORS)}],
        "coverage_sufficient": True,
    }
    result.update(overrides)
    return [result]


def memory_update(cid="claim-color", cert=None, relation="supports"):
    return {
        "claim_id": cid,
        "coverage": "sufficient",
        "gap": None,
        "observations": [
            {
                "observation_id": "",
                "claim_id": cid,
                "relation": relation,
                "text": "The vehicle is blue.",
                "source": {"type": "memory", "record_id": cid},
            }
        ],
        "closure_evidence": certificate() if cert is None else cert,
    }


@pytest.mark.parametrize("case", ["missing", "scope", "coverage", "context"])
def test_declared_sufficient_cannot_bypass_requirement_gate(tmp_path, case):
    path = setup_gate(tmp_path)
    update = memory_update()
    if case == "missing":
        update["closure_evidence"] = []
    elif case == "scope":
        update["closure_evidence"][0]["evidence"][0]["applicability"]["state"] = (
            "after turn"
        )
    elif case == "coverage":
        update["closure_evidence"][0]["coverage_sufficient"] = False
    else:
        update["observations"][0]["relation"] = "context"
    current = ledger.merge_memory(workflow.read(path), [update])
    assert current["status"] == "in_progress"
    assert current["claims"][0]["closure_blockers"]
    assert ledger._needs_inspection(current["claims"][0])


def test_complete_memory_certificate_closes_and_tampering_fails(tmp_path):
    path = setup_gate(tmp_path)
    current = ledger.merge_memory(workflow.read(path), [memory_update()])
    assert current["status"] == "answered"
    current["closure_certificates"]["claim-color"] = []
    with pytest.raises(ValueError, match="Stale"):
        ledger.validate_ledger(current)


def test_dependencies_are_recomputed_after_prerequisite_resolves(tmp_path):
    path = setup_gate(tmp_path, ("claim-color", "claim-motion"), dependency=True)
    current = ledger.merge_memory(workflow.read(path), [memory_update("claim-motion")])
    assert "Prerequisite" in current["claims"][1]["closure_blockers"][0]
    current = ledger.merge_memory(current, [memory_update()])
    assert current["status"] == "answered"


def test_unknown_cyclic_dependencies_rejected(tmp_path):
    path = setup_gate(tmp_path, ("claim-color", "claim-motion"), dependency=True)
    current = workflow.read(path)
    contract = current["closure_contract"]
    contract["claim-color"]["depends_on"] = ["claim-motion"]
    with pytest.raises(ValueError, match="Cyclic"):
        gate.validate_contract(contract, current["plan"])
    contract["claim-color"]["depends_on"] = ["claim-other"]
    with pytest.raises(ValueError, match="Dependencies"):
        gate.validate_contract(contract, current["plan"])


def test_visual_merge_requires_certificate_and_retains_receipts(tmp_path, monkeypatch):
    import vlm_inspection as vlm
    from test_inspection_workflow import response

    monkeypatch.setattr(vlm, "allowed_sensors_from_attempt_context", lambda: ())
    monkeypatch.setattr(vlm, "_run_vlm", lambda **kw: response())
    path = setup_gate(tmp_path)
    batch = workflow.run_batch(path, [spec()], None)
    batch_path = path.parent / "rounds/round-1/batch.json"
    with pytest.raises(ValueError, match="closure_evidence"):
        workflow.merge_batch(path, batch_path, [assessment(batch["results"][0])])
    current = workflow.merge_batch(
        path,
        batch_path,
        [assessment(batch["results"][0], closure_evidence=certificate())],
    )
    assert current["status"] == "answered"
    assert current["vlm_calls_used"] == 1
    assert (
        workflow.merge_batch(
            path,
            batch_path,
            [assessment(batch["results"][0], closure_evidence=certificate())],
        )
        == current
    )


def test_foreign_and_unknown_citations_rejected(tmp_path):
    path = setup_gate(tmp_path)
    update = memory_update()
    update["closure_evidence"][0]["evidence"] = [
        {"observation_id": "obs-not-found", "applicability": ANCHORS}
    ]
    with pytest.raises(ValueError, match="citation"):
        ledger.merge_memory(workflow.read(path), [update])


def test_unresolved_can_still_answer_and_names_blockers(tmp_path):
    path = setup_gate(tmp_path)
    current = ledger.merge_memory(workflow.read(path), [memory_update(cert=[])])
    current.update(status="unresolved", stop_reason="budget_exhausted")
    result = ledger.final_result(current, str(tmp_path), "A")
    assert result["answer"] == "A"
    assert result["evidence_status"] == "unresolved"
    assert "missing evidence bindings" in result["unresolved_gaps"][0]["gap"]


def test_public_cli_rejects_legacy_publication(tmp_path):
    path = initialize(tmp_path)
    with pytest.raises(ValueError, match="closure_contract"):
        ledger.main(
            [
                "final-result",
                "--ledger",
                str(path),
                "--artifact-dir",
                str(tmp_path),
                "--answer",
                "A",
            ]
        )
