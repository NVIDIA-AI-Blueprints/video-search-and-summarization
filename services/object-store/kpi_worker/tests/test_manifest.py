from kpi_worker.core.manifest import (
    hit_size_bytes,
    manifest_line_bytes,
    to_manifest_line,
)


def _hit():
    return {
        "_id": "aoi_m_1:uuid:aoi_inspection:0",
        "_source": {
            "metadata": {
                "source": "deft-aoi/abc/x.jpg",
                "content_metadata": {
                    "objStorePath": "deft-aoi/abc/x.jpg",
                    "imageSize": 1234,
                    "machineId": "M-1",
                },
            }
        },
    }


def test_maps_core_fields():
    line = to_manifest_line(_hit())
    assert line["id"] == "aoi_m_1:uuid:aoi_inspection:0"
    assert line["file_path"] == "deft-aoi/abc/x.jpg"
    assert line["size_byte"] == 1234
    assert line["metadata"]["machineId"] == "M-1"


def test_file_path_falls_back_to_source_when_no_obj_store_path():
    hit = {"_id": "x", "_source": {"metadata": {"source": "s3://a", "content_metadata": {}}}}
    assert to_manifest_line(hit)["file_path"] == "s3://a"


def test_manifest_line_bytes_is_newline_terminated_json():
    b = manifest_line_bytes(_hit())
    assert b.endswith(b"\n")
    assert b.count(b"\n") == 1


def test_hit_size_bytes():
    assert hit_size_bytes(_hit()) == 1234


def test_hit_size_bytes_defaults_zero_when_missing():
    assert hit_size_bytes({"_source": {}}) == 0
    assert hit_size_bytes({"_source": {"metadata": {"content_metadata": {}}}}) == 0
