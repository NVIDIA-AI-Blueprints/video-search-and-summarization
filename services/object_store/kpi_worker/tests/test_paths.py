from kpi_worker.core import paths


def test_manifest_key():
    assert paths.manifest_key("kpi1", 2) == "kpi/kpi1/manifest2.jsonl"


def test_kpi_key():
    assert paths.kpi_key("kpi1") == "kpi/kpi1/kpi.json"


def test_status_prefix_and_key():
    assert paths.status_prefix("kpi1") == "kpi/kpi1/_status/"
    assert paths.status_key("kpi1", 0) == "kpi/kpi1/_status/slice0.json"


def test_status_key_is_under_status_prefix():
    assert paths.status_key("kpi1", 5).startswith(paths.status_prefix("kpi1"))
