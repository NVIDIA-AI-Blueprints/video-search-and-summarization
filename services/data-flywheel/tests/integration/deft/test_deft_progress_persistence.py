"""The `deft` block must survive contact with a real MongoDB.

The unit tests assert the *shape* of the update, which is necessary but was not
sufficient: an earlier revision of this code used dotted paths (`deft.run_id`),
passed every mocked test, and failed against a real server with

    Cannot create field 'run_id' in element {deft: null}

because `FlywheelRun.to_mongo()` is a plain `model_dump()` and writes an
explicit `deft: null` on every insert. The writers are best-effort, so the
failure was silent -- a warning in a log and a field that never appeared.

These tests run the writers against a live MongoDB for that reason.
"""

from datetime import datetime

import pytest
from bson import ObjectId

from src.api.db_manager import TaskDBManager
from src.api.models import FlywheelRun


@pytest.fixture
def db_manager(mongo_db):
    return TaskDBManager()


@pytest.fixture
def run_id(mongo_db):
    """A FlywheelRun inserted exactly the way production inserts one."""
    run = FlywheelRun(
        workload_id="wl-deft-persist",
        client_id="cl-deft-persist",
        started_at=datetime.utcnow(),
        num_records=0,
        nims=[],
    )
    doc = run.to_mongo()
    # The precondition that broke the dotted-path version. If this ever stops
    # holding, the null-safe base in _deft_merge is still correct -- but the
    # regression it guards has changed shape and this test should be revisited.
    assert doc.get("deft", "absent") is None, "to_mongo() no longer writes deft: null"
    inserted = mongo_db.flywheel_runs.insert_one(doc)
    yield inserted.inserted_id
    mongo_db.flywheel_runs.delete_one({"_id": inserted.inserted_id})


def _deft(mongo_db, _id: ObjectId) -> dict:
    return mongo_db.flywheel_runs.find_one({"_id": _id})["deft"]


def test_run_id_writes_over_an_explicit_null_parent(db_manager, mongo_db, run_id):
    db_manager.record_deft_run_id(run_id, "run-9")
    assert _deft(mongo_db, run_id)["run_id"] == "run-9"


def test_progress_writes_over_an_explicit_null_parent(db_manager, mongo_db, run_id):
    now = datetime.utcnow()
    db_manager.record_deft_progress(run_id, "run-9", {"current_iteration": 1}, now)
    deft = _deft(mongo_db, run_id)
    assert deft["run_id"] == "run-9"
    assert deft["progress"] == {"current_iteration": 1}


def test_run_id_write_does_not_erase_recorded_progress(db_manager, mongo_db, run_id):
    """The ordering production actually uses is id-then-progress, but a retry or
    a second start must not cost the progress already reported."""
    db_manager.record_deft_progress(run_id, "run-9", {"current_iteration": 4}, datetime.utcnow())
    db_manager.record_deft_run_id(run_id, "run-9")
    assert _deft(mongo_db, run_id)["progress"] == {"current_iteration": 4}


def test_progress_write_preserves_unrelated_siblings(db_manager, mongo_db, run_id):
    """Per-iteration metrics are meant to accumulate under `deft`; a progress
    write lands every ~15s for hours and must not clobber them."""
    mongo_db.flywheel_runs.update_one(
        {"_id": run_id}, [{"$set": {"deft": {"$literal": {"metrics": {"iter1": {"auc": 0.87}}}}}}]
    )
    db_manager.record_deft_progress(run_id, "run-9", {"current_iteration": 2}, datetime.utcnow())
    deft = _deft(mongo_db, run_id)
    assert deft["metrics"] == {"iter1": {"auc": 0.87}}
    assert deft["progress"] == {"current_iteration": 2}


def test_free_form_detail_is_stored_as_data_not_evaluated(db_manager, mongo_db, run_id):
    """DEFT's `detail` is deliberately free-form (deft-service !16).

    The update is an aggregation pipeline, where a document's values are
    expressions -- so a key that LOOKS like an operator would be interpreted
    rather than stored without the `$literal` wrap. DEFT owns this payload and
    can add any key, so the guard has to hold for a hostile one, not just for
    the well-behaved `epoch`/`eta` keys TAO happens to emit today.
    """
    detail = {"epoch": 2, "max_epoch": 29, "eta": "0:40:26", "$set": "not-an-operator"}
    db_manager.record_deft_progress(
        run_id, "run-9", {"current_step": "train", "detail": detail}, datetime.utcnow()
    )
    stored = _deft(mongo_db, run_id)["progress"]["detail"]
    assert stored == detail
    assert stored["$set"] == "not-an-operator", "operator-shaped key was interpreted"


def test_stage_writes_over_an_explicit_null_parent(db_manager, mongo_db, run_id):
    now = datetime.utcnow()
    db_manager.record_deft_stage(run_id, "select_and_snapshot", now)
    deft = _deft(mongo_db, run_id)
    assert deft["stage"] == "select_and_snapshot"
    assert deft["stage_started_at"] is not None


def test_stage_transitions_do_not_erase_progress_or_run_id(db_manager, mongo_db, run_id):
    """The real interleaving: id, then a stage, then progress, then the next
    stage. Every write must leave the others standing."""
    db_manager.record_deft_run_id(run_id, "run-9")
    db_manager.record_deft_stage(run_id, "run_deft_training", datetime.utcnow())
    db_manager.record_deft_progress(run_id, "run-9", {"current_iteration": 3}, datetime.utcnow())
    db_manager.record_deft_stage(run_id, "run_offline_eval_gate", datetime.utcnow())

    deft = _deft(mongo_db, run_id)
    assert deft["stage"] == "run_offline_eval_gate"
    assert deft["run_id"] == "run-9"
    assert deft["progress"] == {"current_iteration": 3}, "progress lost on stage change"


def test_every_stage_bucket_accumulates_rather_than_replacing(db_manager, mongo_db, run_id):
    """The whole point of the merge: six stages each own a different key and
    write at different times, and the block must end up holding all of them."""
    db_manager.record_deft_run_id(run_id, "run-9")
    db_manager.record_deft_stage(run_id, "select_and_snapshot", datetime.utcnow())
    db_manager.record_deft_fields(
        run_id,
        {"use_case": "aoi", "model_name": "changenet_classify", "data": {"snapshot_id": "snap-1"}},
    )
    db_manager.record_deft_fields(run_id, {"baseline": {"checkpoint": "s3://seed/model.pth"}})
    db_manager.record_deft_progress(run_id, "run-9", {"current_iteration": 2}, datetime.utcnow())
    db_manager.record_deft_fields(run_id, {"result": {"best_iteration": 3}})
    db_manager.record_deft_fields(run_id, {"gate": {"passed": False, "unmet": ["FAR_pct < 200"]}})
    db_manager.record_deft_fields(run_id, {"candidate": {"version": 7, "registered": True}})
    db_manager.record_deft_fields(run_id, {"promotion": {"state": "pending_approval"}})

    deft = _deft(mongo_db, run_id)
    assert deft["run_id"] == "run-9"
    assert deft["stage"] == "select_and_snapshot"
    assert deft["use_case"] == "aoi"
    assert deft["data"]["snapshot_id"] == "snap-1"
    assert deft["baseline"]["checkpoint"] == "s3://seed/model.pth"
    assert deft["progress"] == {"current_iteration": 2}
    assert deft["result"]["best_iteration"] == 3
    assert deft["gate"]["unmet"] == ["FAR_pct < 200"]
    assert deft["candidate"]["version"] == 7
    assert deft["promotion"]["state"] == "pending_approval"


def test_a_bucket_rewrite_replaces_only_that_bucket(db_manager, mongo_db, run_id):
    """Promotion state is republished as it advances; that must not disturb the
    other buckets, and must fully replace its own."""
    db_manager.record_deft_fields(
        run_id, {"promotion": {"id": "p1", "state": "pending_approval", "pending_gate": "canary"}}
    )
    db_manager.record_deft_fields(run_id, {"gate": {"passed": True}})
    db_manager.record_deft_fields(
        run_id, {"promotion": {"id": "p1", "state": "promoted", "pending_gate": None}}
    )

    deft = _deft(mongo_db, run_id)
    assert deft["promotion"]["state"] == "promoted"
    assert deft["promotion"]["pending_gate"] is None
    assert deft["gate"] == {"passed": True}, "unrelated bucket disturbed"


def test_empty_fields_is_a_no_op(db_manager, mongo_db, run_id):
    db_manager.record_deft_fields(run_id, {})
    assert mongo_db.flywheel_runs.find_one({"_id": run_id})["deft"] is None
