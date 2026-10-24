"""The DEFT block must actually reach `GET /api/jobs/{id}`.

`DeftRunDetailResponse` mirrors `DeftRunDetail`, and the mapping between them
was written field by field. That is a shape that fails silently: a writer adds a
field, the copy does not, and the value lands in Mongo and never in the API.

It is not hypothetical. The mapping carried three fields while the model held
sixteen, so an entire visibility change reached the database and was invisible
to every caller -- with no error anywhere, because dropping a field during
construction is not an error.
"""

from datetime import datetime
from unittest.mock import patch

from bson import ObjectId

from src.api.job_service import get_job_details
from src.api.models import DeftRunDetail
from src.api.schemas import DeftRunDetailResponse


def test_deft_detail_response_mirrors_the_stored_model():
    """The guard. Any field added to one side must be added to the other.

    Cheaper than discovering the omission from a caller who cannot see a field
    the database has held all along.
    """
    stored = set(DeftRunDetail.model_fields)
    exposed = set(DeftRunDetailResponse.model_fields)
    assert stored == exposed, (
        f"DEFT detail is stored but not exposed: {sorted(stored - exposed)}; "
        f"exposed but not stored: {sorted(exposed - stored)}"
    )


def _run_doc(flywheel_run_id: ObjectId, deft: dict) -> dict:
    return {
        "_id": flywheel_run_id,
        "workload_id": "wl",
        "client_id": "cl",
        "started_at": datetime.utcnow(),
        "finished_at": None,
        "num_records": 0,
        "datasets": [],
        "error": None,
        "deft": deft,
    }


def test_get_job_details_exposes_every_recorded_deft_field(mock_db):
    """End to end through the real read path, with a full block."""
    fw_id = ObjectId()
    deft = {
        "run_id": "841eb4bc",
        "progress": {"current_iteration": 5, "current_step": "eval"},
        "progress_at": datetime.utcnow(),
        "stage": "wait_for_promotion",
        "stage_started_at": datetime.utcnow(),
        "use_case": "aoi",
        "model_name": "changenet_classify",
        "last_poll_at": datetime.utcnow(),
        "poll_errors": 2,
        "timeout_s": 10800.0,
        "data": {"snapshot_id": "snap-1", "snapshot_uri": "s3://s/", "kpi_set_uri": "s3://k/"},
        "baseline": {"checkpoint": "s3://seed/model.pth"},
        "result": {
            "convergence_reason": "kpi_met",
            "best_iteration": 3,
            "eval_metrics": {"FAR_pct": 12.5, "AUC": 0.87},
            "threshold": 0.63,
            "checkpoint": "s3://cand/model.pth",
        },
        "gate": {"passed": False, "metrics": {"FAR_pct": 240.0}, "unmet": ["FAR_pct < 200.0"]},
        "candidate": {"version": "7", "registered": True},
        "promotion": {"id": "p1", "state": "pending_approval", "pending_gate": "canary"},
    }
    mock_db.flywheel_runs.find_one.return_value = _run_doc(fw_id, deft)
    mock_db.nims.find.return_value = []
    mock_db.evaluations.find.return_value = []
    mock_db.customizations.find.return_value = []
    mock_db.llm_judge_runs.find_one.return_value = None

    with patch("src.api.job_service.get_db", return_value=mock_db):
        payload = get_job_details(str(fw_id)).model_dump()

    got = payload["deft"]
    assert got is not None, "deft block missing from the response"
    for key, expected in deft.items():
        assert got.get(key) == expected, f"{key} did not survive the mapping"

    # The four an operator most often needs, asserted by name so a regression
    # names the field rather than a diff.
    assert got["stage"] == "wait_for_promotion"
    assert got["gate"]["unmet"] == ["FAR_pct < 200.0"]
    assert got["baseline"]["checkpoint"] == "s3://seed/model.pth"
    assert got["promotion"]["state"] == "pending_approval"


def test_deft_block_is_absent_for_non_deft_jobs(mock_db):
    """`deft: null` must stay null -- it is how a caller tells an llm_job from a
    DEFT run, and is distinct from a DEFT run that has reported nothing yet."""
    fw_id = ObjectId()
    doc = _run_doc(fw_id, None)
    mock_db.flywheel_runs.find_one.return_value = doc
    mock_db.nims.find.return_value = []
    mock_db.evaluations.find.return_value = []
    mock_db.customizations.find.return_value = []
    mock_db.llm_judge_runs.find_one.return_value = None

    with patch("src.api.job_service.get_db", return_value=mock_db):
        payload = get_job_details(str(fw_id)).model_dump()

    assert payload["deft"] is None
