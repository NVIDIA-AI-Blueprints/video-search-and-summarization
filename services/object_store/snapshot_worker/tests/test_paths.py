from snapshot_worker.core import paths


def test_manifest_key():
    assert paths.manifest_key("snap1", 2) == "snapshots/snap1/manifest2.jsonl"


def test_snapshot_key():
    assert paths.snapshot_key("snap1") == "snapshots/snap1/snapshot.json"


def test_status_prefix_and_key():
    assert paths.status_prefix("snap1") == "snapshots/snap1/_status/"
    assert paths.status_key("snap1", 0) == "snapshots/snap1/_status/slice0.json"


def test_status_key_is_under_status_prefix():
    assert paths.status_key("snap1", 5).startswith(paths.status_prefix("snap1"))
