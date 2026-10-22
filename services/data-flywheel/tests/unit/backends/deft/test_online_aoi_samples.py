"""Eval-set manifest records -> per-target online samples.

The Storage Backend's eval-set manifests are *object* records (one per board),
not scored samples. These tests pin the flattening down to one sample per
component target, which is the population online FAR is measured over.
"""

from src.backends.deft.online_aoi_samples import (
    GOOD,
    NOT_GOOD,
    ground_truth,
    records_to_samples,
)


def _live_record(crops, *, file_path="deft-aoi/abc/board_000_board_img.png"):
    """An AOI live-catalogue object record as it appears in an eval manifest."""
    return {
        "file_path": file_path,
        "obj_version": "v1",
        "sha256": "deadbeef",
        "size_byte": 1234,
        "metadata": {
            "uuid": "board-uuid",
            "cropInfo": crops,
            "inferenceSummary": {
                "deployment_type": "shadow",
                "model_name": "changenet_classify",
                "cropCount": len(crops),
                "completedCount": len(crops),
                "failedCount": 0,
                "status": "completed",
            },
        },
    }


def _crop(
    cid,
    *,
    defect_type="PASS",
    new_verdict="PASS",
    old_verdict="PASS",
    new_score=0.1,
    old_score=0.1,
    confirmed=None,
):
    crop = {
        "id": cid,
        "label": "Resistor",
        "aoiDefectInfo": {"defectType": defect_type},
        "inference": {
            "inspectionId": f"insp_{cid}",
            "score": new_score,
            "verdict": new_verdict,
            "threshold": 0.5,
            "status": "completed",
            "old_model": {
                "model_name": "m",
                "version": "1",
                "score": old_score,
                "verdict": old_verdict,
            },
            "new_model": {
                "model_name": "m",
                "version": "2",
                "score": new_score,
                "verdict": new_verdict,
            },
        },
    }
    if confirmed is not None:
        crop["confirmedLabel"] = confirmed
    return crop


# --- flattening -------------------------------------------------------------


def test_one_sample_per_crop_not_per_board():
    rec = _live_record([_crop("U1"), _crop("R2"), _crop("C3")])
    samples = records_to_samples([rec])
    assert len(samples) == 3
    assert {s["target_id"] for s in samples} == {"insp_U1", "insp_R2", "insp_C3"}


def test_candidate_call_comes_from_new_model_and_baseline_from_old_model():
    rec = _live_record(
        [_crop("U1", new_verdict="DEFECT", new_score=0.9, old_verdict="PASS", old_score=0.2)]
    )
    (s,) = records_to_samples([rec])
    assert s["ai_classification"] == NOT_GOOD
    assert s["ai_score"] == 0.9
    assert s["baseline_classification"] == GOOD


def test_reference_label_from_aoi_defect_type():
    good, bad = records_to_samples(
        [_live_record([_crop("U1", defect_type="PASS"), _crop("R2", defect_type="MISSING")])]
    )
    assert good["reference_label"] == GOOD
    assert bad["reference_label"] == NOT_GOOD


def test_operator_confirmation_wins_over_reference_label():
    rec = _live_record([_crop("U1", defect_type="PASS", confirmed="NotGood")])
    (s,) = records_to_samples([rec])
    assert s["confirmed_label"] == NOT_GOOD
    assert ground_truth(s) == NOT_GOOD


def test_ground_truth_falls_back_to_reference_label():
    rec = _live_record([_crop("U1", defect_type="PASS")])
    (s,) = records_to_samples([rec])
    assert s["confirmed_label"] is None
    assert ground_truth(s) == GOOD


# --- robustness -------------------------------------------------------------


def test_crop_without_inference_is_skipped_not_scored_as_good():
    """A target the candidate never classified must not count as a PASS.

    This is the MR !574 invariant: never score a target the candidate did not
    classify. Such a crop yields no sample at all.
    """
    rec = _live_record([{"id": "U1", "aoiDefectInfo": {"defectType": "PASS"}}])
    assert records_to_samples([rec]) == []


def test_failed_inference_is_skipped_even_when_it_carries_a_verdict():
    """A non-completed crop must not be scored off stale inference fields.

    ``failedCount`` is part of the AOI contract, so a crop can report
    ``status: failed`` while still carrying a verdict from an earlier attempt.
    Counting that in FAR would score a target the candidate never really called.
    """
    crop = _crop("U1", new_verdict="DEFECT")
    crop["inference"]["status"] = "failed"
    assert crop["inference"]["verdict"] == "DEFECT"  # fields deliberately intact
    assert crop["inference"]["new_model"]["verdict"] == "DEFECT"
    assert records_to_samples([_live_record([crop])]) == []


def test_pending_inference_is_skipped():
    crop = _crop("U1")
    crop["inference"]["status"] = "in_progress"
    assert records_to_samples([_live_record([crop])]) == []


def test_status_is_matched_case_insensitively():
    crop = _crop("U1", new_verdict="DEFECT")
    crop["inference"]["status"] = "COMPLETED"
    (s,) = records_to_samples([_live_record([crop])])
    assert s["ai_classification"] == NOT_GOOD


def test_absent_status_is_still_scored():
    """Records that carry no status at all stay readable (back-compat)."""
    crop = _crop("U1", new_verdict="DEFECT")
    crop["inference"].pop("status")
    (s,) = records_to_samples([_live_record([crop])])
    assert s["ai_classification"] == NOT_GOOD


def test_records_without_crops_yield_nothing():
    assert records_to_samples([_live_record([])]) == []
    assert records_to_samples([{"file_path": "x", "metadata": {}}]) == []
    assert records_to_samples([{}]) == []
    assert records_to_samples([]) == []


def test_malformed_records_do_not_raise():
    assert records_to_samples([None, 42, "nonsense", {"metadata": None}]) == []


# --- the standardized nv-aoi/v1 shape ---------------------------------------


def test_nv_aoi_standardized_inference_result_targets():
    rec = {
        "nv_aoi": {"inspectionId": "insp_1", "imageInfo": {"cropInfo": []}},
        "inference_result": {
            "model_id": "aoi-secondary-inspection:1.0.0",
            "targets": [
                {"component_id": "R17", "classification": "NotGood", "confidence": 0.98},
                {"component_id": "C4", "classification": "Good", "confidence": 0.11},
            ],
        },
    }
    samples = records_to_samples([rec])
    assert len(samples) == 2
    assert samples[0]["target_id"] == "insp_1:R17"
    assert samples[0]["ai_classification"] == NOT_GOOD
    assert samples[0]["ai_confidence"] == 0.98
    assert samples[1]["ai_classification"] == GOOD


def test_verdict_vocabulary_is_normalized():
    for raw, expected in (
        ("DEFECT", NOT_GOOD),
        ("defect", NOT_GOOD),
        ("NotGood", NOT_GOOD),
        ("PASS", GOOD),
        ("Good", GOOD),
        ("ok", GOOD),
    ):
        rec = _live_record([_crop("U1", new_verdict=raw)])
        (s,) = records_to_samples([rec])
        assert s["ai_classification"] == expected, raw


def test_unknown_verdict_yields_no_classification():
    rec = _live_record([_crop("U1", new_verdict="???")])
    assert records_to_samples([rec]) == []


# --- real NVPAW_DET annotation vocabulary ------------------------------------
# The operator-confirmation source (aoi-active-learning!51) writes the raw
# annotation label into confirmed_label. That vocabulary is open: PCB defects
# ("Mouse Bite", "Hole Breakout", "Spur"), the generic "defect", and component
# names that mean the part is MISSING ("Capacitor", "Resistor"). Parsing it with
# the closed verdict vocabulary dropped every one of them.


def test_operator_confirmation_accepts_real_defect_type_names():
    for label in (
        "defect",
        "Mouse Bite",
        "Hole Breakout",
        "Spur",
        "Short",
        "Open",
        "Capacitor",
        "Resistor",
        "Metal Oxide Varistor",
        "Missing Hole",
        "defected",
    ):
        rec = _live_record([_crop("U1", defect_type="PASS", confirmed=label)])
        (s,) = records_to_samples([rec])
        assert s["confirmed_label"] == NOT_GOOD, label
        # Operator confirmation must WIN over the machine's PASS.
        assert ground_truth(s) == NOT_GOOD, label


def test_operator_can_confirm_a_target_is_actually_good():
    """The machine cried defect; the operator cleared it. That is a false alarm."""
    rec = _live_record([_crop("U1", defect_type="MISSING", confirmed="PASS", new_verdict="DEFECT")])
    (s,) = records_to_samples([rec])
    assert s["confirmed_label"] == GOOD
    assert ground_truth(s) == GOOD


def test_blank_confirmation_is_not_a_confirmation():
    for blank in ("", "   ", None):
        rec = _live_record([_crop("U1", defect_type="PASS", confirmed=blank)])
        got = records_to_samples([rec])
        assert got and got[0]["confirmed_label"] is None, repr(blank)
