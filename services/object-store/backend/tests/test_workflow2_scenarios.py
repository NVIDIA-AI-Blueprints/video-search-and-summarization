"""Exhaustive scenario coverage for the two Workflow 2 features (task 4.5):

  Feature A — Outbound trigger: BE -> AOI Client POST /api/inference
              (backend/shared/aoi_client.py, backend/api/aoi_trigger.py,
               trigger point in backend/api/service.py)

  Feature B — Inbound sink: AL Archiver -> BE POST /api/inference-results -> ES upsert
              (backend/api/inference_{schemas,service,router}.py,
               backend/shared/elasticsearch.py)

Scenario IDs (A*/B*) are referenced by test-reports/workflow2-test-report.md.
Real-ES integration tests are gated on ES_E2E=1 so the suite stays green offline.
"""

import base64
import os
import re
import asyncio

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.api.aoi_trigger import build_inference_request, resolve_golden_ref_b64
from backend.api.inference_schemas import CropResult, InferenceResultRecord
from backend.api.inference_service import (
    _crop_inference,
    _inference_summary,
    _metadata_from_storage,
    merge_inference_into_metadata,
)
from backend.api.metadata import (
    AOIMetadata,
    CropInference,
    InferenceSummary,
)
from backend.api.schemas import FailedImage, ImageItem, UploadedImage
from backend.api.service import upload_image
from backend.config import settings
from backend.shared.aoi_client import (
    NON_RETRYABLE_STATUS_CODES,
    RETRYABLE_STATUS_CODES,
    AoiClientService,
    _backoff_delay,
)
from backend.shared.elasticsearch import (
    OBJ_STORE_PATH_FIELD,
    ElasticsearchService,
    aoi_metadata_to_es_content_metadata,
)

from backend.tests.conftest import (
    VALID_B64,
    VALID_INFERENCE_RESULT,
    VALID_METADATA,
    VALID_METADATA_WITH_CROPS,
    drain_aoi_client,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _mk_http(responses: list | None = None, side_effect=None):
    """A fake httpx.AsyncClient whose .post yields the given responses."""
    http = MagicMock()
    http.aclose = AsyncMock()
    if side_effect is not None:
        http.post = AsyncMock(side_effect=side_effect)
    else:
        http.post = AsyncMock(side_effect=list(responses or []))
    return http


def _resp(status_code, json_body=None, text=""):
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    if json_body is not None:
        r.json.return_value = json_body
    else:
        r.json.side_effect = ValueError("no json")
    r.raise_for_status = MagicMock()
    if status_code >= 400:
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=r
        )
    return r


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    """Zero out backoff so retry loops don't actually sleep."""
    monkeypatch.setattr(settings, "aoi_client_retry_base_delay_s", 0.0)
    monkeypatch.setattr(settings, "aoi_client_retry_max_delay_s", 0.0)
    monkeypatch.setattr(settings, "aoi_client_max_retries", 3)
    yield


def _metadata(md_dict):
    return AOIMetadata.model_validate(
        {**md_dict, "objStorePath": "deft-aoi/x/board.png"}
    )


def _board_metadata(path: str = "p/x.png") -> AOIMetadata:
    return AOIMetadata.model_validate(
        {**VALID_METADATA_WITH_CROPS, "objStorePath": path}
    )


def _metadata_with_inference(path: str = "p/x.png") -> AOIMetadata:
    record = InferenceResultRecord.model_validate(
        {**VALID_INFERENCE_RESULT, "objStorePath": path}
    )
    return merge_inference_into_metadata(_board_metadata(path), record)


# ==========================================================================
# Feature A — Outbound trigger
# ==========================================================================

# ---- A1: AoiClientService retry / response classification -----------------


async def test_A1_1_accepts_202_reads_cropcount():
    http = _mk_http([_resp(202, {"cropCount": 3})])
    svc = AoiClientService(client=http)
    svc.schedule_trigger({"sampleImage": "x"}, "k")
    await drain_aoi_client(svc)
    assert http.post.await_count == 1
    await svc.close()


async def test_A1_2_202_missing_cropcount_still_ok():
    http = _mk_http([_resp(202, {})])  # json present but no cropCount key
    svc = AoiClientService(client=http)
    svc.schedule_trigger({"sampleImage": "x"}, "k")
    await drain_aoi_client(svc)
    assert http.post.await_count == 1
    await svc.close()


async def test_A1_2b_202_non_json_body_still_ok():
    """Bug #2 regression: a 202 with an empty/non-JSON body must not crash the trigger."""
    r = _resp(202, json_body=None)  # .json() raises ValueError
    http = _mk_http([r])
    svc = AoiClientService(client=http)
    svc.schedule_trigger({"sampleImage": "x"}, "k")
    await drain_aoi_client(svc)
    assert http.post.await_count == 1
    await svc.close()


@pytest.mark.parametrize("code", sorted(NON_RETRYABLE_STATUS_CODES))
async def test_A1_3_non_retryable_single_call(code):
    http = _mk_http([_resp(code, text="rejected")])
    svc = AoiClientService(client=http)
    svc.schedule_trigger({}, "k")
    await drain_aoi_client(svc)
    assert http.post.await_count == 1
    await svc.close()


@pytest.mark.parametrize("code", sorted(RETRYABLE_STATUS_CODES))
async def test_A1_4_retryable_exhausts_then_false(code):
    http = _mk_http([_resp(code) for _ in range(settings.aoi_client_max_retries + 1)])
    svc = AoiClientService(client=http)
    svc.schedule_trigger({}, "k")
    await drain_aoi_client(svc)
    assert http.post.await_count == settings.aoi_client_max_retries + 1  # 4
    await svc.close()


async def test_A1_5_503_then_202_recovers():
    http = _mk_http([_resp(503), _resp(202, {"cropCount": 1})])
    svc = AoiClientService(client=http)
    svc.schedule_trigger({}, "k")
    await drain_aoi_client(svc)
    assert http.post.await_count == 2
    await svc.close()


@pytest.mark.parametrize("code", [200, 201, 204, 301, 400, 404, 418])
async def test_A1_6_unexpected_status_no_retry_false(code):
    http = _mk_http([_resp(code, text="weird")])
    svc = AoiClientService(client=http)
    svc.schedule_trigger({}, "k")
    await drain_aoi_client(svc)
    assert http.post.await_count == 1
    await svc.close()


async def test_A1_7_http_error_retried_then_false():
    http = _mk_http(side_effect=httpx.ConnectError("down"))
    svc = AoiClientService(client=http)
    svc.schedule_trigger({}, "k")
    await drain_aoi_client(svc)
    assert http.post.await_count == settings.aoi_client_max_retries + 1
    await svc.close()


async def test_A1_8_http_error_then_recovers():
    http = _mk_http(
        side_effect=[httpx.ConnectError("down"), _resp(202, {"cropCount": 0})]
    )
    svc = AoiClientService(client=http)
    svc.schedule_trigger({}, "k")
    await drain_aoi_client(svc)
    assert http.post.await_count == 2
    await svc.close()


async def test_A1_9_zero_retries_single_attempt(monkeypatch):
    monkeypatch.setattr(settings, "aoi_client_max_retries", 0)
    http = _mk_http([_resp(503)])
    svc = AoiClientService(client=http)
    svc.schedule_trigger({}, "k")
    await drain_aoi_client(svc)
    assert http.post.await_count == 1
    await svc.close()


def test_A1_10_backoff_delay_bounds(monkeypatch):
    monkeypatch.setattr(settings, "aoi_client_retry_base_delay_s", 1.0)
    monkeypatch.setattr(settings, "aoi_client_retry_max_delay_s", 30.0)
    for attempt in range(6):
        d = _backoff_delay(attempt)
        cap = min(1.0 * (2**attempt), 30.0)
        assert 0.5 * cap <= d <= cap  # jitter is [0.5, 1.0] * cap


# ---- A2: background task management ---------------------------------------


async def test_A2_1_schedule_trigger_runs_and_discards():
    http = _mk_http([_resp(202, {"cropCount": 1})])
    svc = AoiClientService(client=http)
    svc.schedule_trigger({"sampleImage": "x"}, "k")
    assert len(svc._tasks) == 1
    await drain_aoi_client(svc)
    assert len(svc._tasks) == 0
    await svc.close()


async def test_A2_2_close_cancels_pending_tasks():
    # a post that blocks forever so the task is still pending at close()
    import asyncio

    async def _hang(*a, **k):
        await asyncio.sleep(3600)

    http = MagicMock()
    http.aclose = AsyncMock()
    http.post = _hang
    svc = AoiClientService(client=http)
    svc.schedule_trigger({}, "k")
    assert len(svc._tasks) == 1
    await svc.close()  # must not hang
    http.aclose.assert_awaited()


# ---- A3: build_inference_request / golden-ref -----------------------------


async def test_A3_1_maps_crops_and_base64(mock_storage):
    md = _metadata(VALID_METADATA_WITH_CROPS)
    req = await build_inference_request(b"hello-bytes", md, mock_storage)
    assert req["sampleImage"] == base64.b64encode(b"hello-bytes").decode()
    assert req["metadata"]["objStorePath"] == md.objStorePath
    crop = req["metadata"]["imageInfo"]["cropInfo"][0]
    assert crop["id"] == "U7"
    assert crop["crop"]["coordinates"] == {
        "left": 10,
        "top": 20,
        "width": 64,
        "height": 64,
    }


async def test_A3_2_multiple_crops_order_preserved(mock_storage):
    md_dict = {
        **VALID_METADATA,
        "imageInfo": {
            "cropInfo": [
                {
                    "id": f"U{i}",
                    "label": "IC",
                    "referenceLabels": None,
                    "crop": {
                        "cropType": "bbox",
                        "coordinates": {"left": i, "top": i, "width": 8, "height": 8},
                    },
                    "aoiDefectInfo": {"defectType": "bridge"},
                }
                for i in range(4)
            ]
        },
    }
    req = await build_inference_request(b"x", _metadata(md_dict), mock_storage)
    assert [c["id"] for c in req["metadata"]["imageInfo"]["cropInfo"]] == [
        "U0",
        "U1",
        "U2",
        "U3",
    ]


async def test_A3_3_golden_ref_dev_flag_uses_sample(monkeypatch, mock_storage):
    monkeypatch.setattr(settings, "dev_use_sample_as_golden_ref", True)
    md = _metadata(VALID_METADATA_WITH_CROPS)
    req = await build_inference_request(b"img", md, mock_storage)
    assert req["goldenRefImage"] == req["sampleImage"]


async def test_A3_4_golden_ref_present_dev_flag_still_sample(monkeypatch, mock_storage):
    monkeypatch.setattr(settings, "dev_use_sample_as_golden_ref", True)
    md_dict = {
        **VALID_METADATA_WITH_CROPS,
        "imageInfo": {
            **VALID_METADATA_WITH_CROPS["imageInfo"],
            "golden-ref": "s3://ref/board.png",
        },
    }
    md = _metadata(md_dict)
    b64 = base64.b64encode(b"img").decode()
    assert await resolve_golden_ref_b64(b64, md, mock_storage) == b64


async def test_A3_4b_golden_ref_warning_message_accurate(monkeypatch, caplog):
    """Bug #5 regression: the warning must only claim 'dev fallback' when the
    fallback is actually used, not when it's about to raise (prod flag, ref set)."""
    import logging

    md_dict = {
        **VALID_METADATA_WITH_CROPS,
        "imageInfo": {
            **VALID_METADATA_WITH_CROPS["imageInfo"],
            "golden-ref": "s3://ref/board.png",
        },
    }
    md = _metadata(md_dict)
    failing_storage = MagicMock()
    failing_storage.get_bytes = AsyncMock(side_effect=Exception("not found"))
    # prod flag: raises, message must NOT claim it used the fallback
    monkeypatch.setattr(settings, "dev_use_sample_as_golden_ref", False)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ValueError):
            await resolve_golden_ref_b64("x", md, failing_storage)
    assert "dev fallback" not in caplog.text
    caplog.clear()
    # dev flag: uses fallback, message SHOULD say so
    monkeypatch.setattr(settings, "dev_use_sample_as_golden_ref", True)
    with caplog.at_level(logging.WARNING):
        await resolve_golden_ref_b64("x", md, failing_storage)
    assert "dev fallback" in caplog.text


async def test_A3_5_golden_ref_absent_prod_flag_uses_sample(monkeypatch, mock_storage):
    monkeypatch.setattr(settings, "dev_use_sample_as_golden_ref", False)
    md = _metadata(VALID_METADATA_WITH_CROPS)  # no golden-ref
    b64 = base64.b64encode(b"img").decode()
    assert await resolve_golden_ref_b64(b64, md, mock_storage) == b64


async def test_A3_6_golden_ref_present_prod_flag_raises(monkeypatch):
    monkeypatch.setattr(settings, "dev_use_sample_as_golden_ref", False)
    md_dict = {
        **VALID_METADATA_WITH_CROPS,
        "imageInfo": {
            **VALID_METADATA_WITH_CROPS["imageInfo"],
            "golden-ref": "s3://ref/board.png",
        },
    }
    md = _metadata(md_dict)
    failing_storage = MagicMock()
    failing_storage.get_bytes = AsyncMock(side_effect=Exception("not found"))
    with pytest.raises(ValueError):
        await resolve_golden_ref_b64("x", md, failing_storage)


# ---- A4: trigger gating in upload_image -----------------------------------


def _image_item(metadata):
    return ImageItem.model_validate(
        {
            "filename": "b.jpg",
            "content_type": "image/jpeg",
            "data": VALID_B64,
            "metadata": metadata,
        }
    )


async def _run_upload(monkeypatch, *, enabled, metadata, aoi_client, kafka_fail=False):
    monkeypatch.setattr(settings, "inference_enabled", enabled)
    storage = MagicMock()
    storage.upload = AsyncMock(return_value="http://s/obj")
    storage.delete = AsyncMock()
    kafka = MagicMock()
    kafka.send = AsyncMock(
        side_effect=RuntimeError("kafka down") if kafka_fail else None
    )
    return await upload_image(storage, kafka, _image_item(metadata), aoi_client)


async def test_A4_1_disabled_no_trigger(monkeypatch):
    client = MagicMock()
    client.schedule_trigger = MagicMock()
    res = await _run_upload(
        monkeypatch,
        enabled=False,
        metadata=VALID_METADATA_WITH_CROPS,
        aoi_client=client,
    )
    assert isinstance(res, UploadedImage)
    client.schedule_trigger.assert_not_called()


async def test_A4_2_no_crops_no_trigger(monkeypatch):
    client = MagicMock()
    client.schedule_trigger = MagicMock()
    res = await _run_upload(
        monkeypatch, enabled=True, metadata=VALID_METADATA, aoi_client=client
    )
    assert isinstance(res, UploadedImage)
    client.schedule_trigger.assert_not_called()


async def test_A4_3_client_none_no_crash(monkeypatch):
    res = await _run_upload(
        monkeypatch, enabled=True, metadata=VALID_METADATA_WITH_CROPS, aoi_client=None
    )
    assert isinstance(res, UploadedImage)


async def test_A4_4_happy_path_schedules_with_key(monkeypatch):
    client = MagicMock()
    client.schedule_trigger = MagicMock()
    res = await _run_upload(
        monkeypatch, enabled=True, metadata=VALID_METADATA_WITH_CROPS, aoi_client=client
    )
    assert isinstance(res, UploadedImage)
    client.schedule_trigger.assert_called_once()
    payload, key = client.schedule_trigger.call_args.args
    assert key == res.storage_key
    assert payload["metadata"]["objStorePath"] == res.storage_key


async def test_A4_5_build_error_caught_upload_still_ok(monkeypatch):
    monkeypatch.setattr(settings, "dev_use_sample_as_golden_ref", False)
    md = {
        **VALID_METADATA_WITH_CROPS,
        "imageInfo": {
            **VALID_METADATA_WITH_CROPS["imageInfo"],
            "golden-ref": "s3://ref",
        },
    }
    client = MagicMock()
    client.schedule_trigger = MagicMock()
    res = await _run_upload(monkeypatch, enabled=True, metadata=md, aoi_client=client)
    assert isinstance(res, UploadedImage)  # upload not failed by trigger error
    client.schedule_trigger.assert_not_called()  # build raised -> nothing scheduled


async def test_A4_6_kafka_fail_no_trigger(monkeypatch):
    client = MagicMock()
    client.schedule_trigger = MagicMock()
    res = await _run_upload(
        monkeypatch,
        enabled=True,
        metadata=VALID_METADATA_WITH_CROPS,
        aoi_client=client,
        kafka_fail=True,
    )
    assert isinstance(res, FailedImage)
    client.schedule_trigger.assert_not_called()


# ==========================================================================
# Feature B — Inbound sink
# ==========================================================================

# ---- B1: schema validation ------------------------------------------------


def test_B1_1_valid_record_parses():
    rec = InferenceResultRecord.model_validate(VALID_INFERENCE_RESULT)
    assert rec.objStorePath == VALID_INFERENCE_RESULT["objStorePath"]


@pytest.mark.parametrize(
    "missing",
    [
        "objStorePath",
        "status",
        "cropCount",
        "completedCount",
        "failedCount",
        "cropResults",
    ],
)
def test_B1_2_missing_required_rejected(missing):
    body = {k: v for k, v in VALID_INFERENCE_RESULT.items() if k != missing}
    with pytest.raises(Exception):
        InferenceResultRecord.model_validate(body)


def test_B1_3_extra_top_level_field_rejected():
    with pytest.raises(Exception):
        InferenceResultRecord.model_validate({**VALID_INFERENCE_RESULT, "surprise": 1})


def test_B1_4_extra_cropresult_field_rejected():
    body = {
        **VALID_INFERENCE_RESULT,
        "cropResults": [{"cropId": "U7", "status": "completed", "surprise": 1}],
    }
    with pytest.raises(Exception):
        InferenceResultRecord.model_validate(body)


def test_B1_5_minimal_cropresult_ok():
    body = {
        **VALID_INFERENCE_RESULT,
        "cropResults": [{"cropId": "U7", "status": "failed"}],
    }
    rec = InferenceResultRecord.model_validate(body)
    assert rec.cropResults[0].score is None


def test_B1_6_empty_cropresults_ok():
    body = {**VALID_INFERENCE_RESULT, "cropResults": []}
    assert InferenceResultRecord.model_validate(body).cropResults == []


def test_B1_7_deployment_type_optional():
    body = {k: v for k, v in VALID_INFERENCE_RESULT.items() if k != "deployment_type"}
    assert InferenceResultRecord.model_validate(body).deployment_type is None


def test_B1_8_model_result_missing_field_rejected():
    body = {
        **VALID_INFERENCE_RESULT,
        "cropResults": [
            {
                "cropId": "U7",
                "status": "completed",
                "old_model": {"version": "v1", "score": 0.1},
            }
        ],
    }  # missing verdict
    with pytest.raises(Exception):
        InferenceResultRecord.model_validate(body)


# ---- B2: inference summary + per-crop inference docs ----------------------


def test_B2_1_crop_inference_doc_includes_models():
    obj_path = VALID_INFERENCE_RESULT["objStorePath"]
    crop = CropResult.model_validate(VALID_INFERENCE_RESULT["cropResults"][0])
    doc = _crop_inference(crop, obj_path).model_dump(exclude_none=True)
    import hashlib
    expected_id = f"{hashlib.sha256(obj_path.encode()).hexdigest()[:32]}_U7"
    assert doc["inspectionId"] == expected_id
    assert "old_model" in doc and "new_model" in doc
    assert doc["old_model"]["version"] == "v1"


def test_B2_2_inference_summary_only_aggregate_fields():
    rec = InferenceResultRecord.model_validate(VALID_INFERENCE_RESULT)
    doc = _inference_summary(rec).model_dump(exclude_none=True)
    assert set(doc) == {
        "status",
        "cropCount",
        "completedCount",
        "failedCount",
        "deployment_type",
        "model_name",
        "updatedAt",
    }
    assert "cropResults" not in doc


def test_B2_3_updated_at_iso_format():
    rec = InferenceResultRecord.model_validate(VALID_INFERENCE_RESULT)
    ts = _inference_summary(rec).updatedAt
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", ts)


def test_B2_4_failed_crop_inference_omits_none_fields():
    import hashlib
    obj_path = VALID_INFERENCE_RESULT["objStorePath"]
    body = {
        **VALID_INFERENCE_RESULT,
        "cropResults": [{"cropId": "U9", "status": "failed"}],
    }
    crop = InferenceResultRecord.model_validate(body).cropResults[0]
    doc = _crop_inference(crop, obj_path).model_dump(exclude_none=True)
    expected_id = f"{hashlib.sha256(obj_path.encode()).hexdigest()[:32]}_U9"
    assert doc == {"inspectionId": expected_id, "status": "failed"}


def _es_hit(index="aoi_m1", doc_id="doc-9", crop_id="U7"):
    return {
        "_index": index,
        "_id": doc_id,
        "_source": {
            "metadata": {
                "content_metadata": {
                    "cropInfo": [{"id": crop_id, "label": "IC"}],
                }
            }
        },
    }


# ---- B3: ElasticsearchService upsert --------------------------------------


async def test_B3_1_upsert_found_updates_correct_target():
    mock_es = MagicMock()
    mock_es.close = AsyncMock()
    mock_es.search = AsyncMock(return_value={"hits": {"hits": [_es_hit()]}})
    mock_es.update = AsyncMock(return_value={"result": "updated"})
    es = ElasticsearchService(client=mock_es)
    metadata = _metadata_with_inference("p/x.png")
    assert await es.upsert_inference_result(metadata) is True
    mock_es.search.assert_awaited_once_with(
        index=settings.elasticsearch_index_pattern,
        query={"term": {OBJ_STORE_PATH_FIELD: "p/x.png"}},
        size=1,
    )
    update_doc = mock_es.update.await_args.kwargs["doc"]
    cm = update_doc["metadata"]["content_metadata"]
    expected = aoi_metadata_to_es_content_metadata(metadata)
    assert cm["inferenceSummary"] == expected["inferenceSummary"]
    assert cm["cropInfo"][0]["inference"] == expected["cropInfo"][0]["inference"]
    assert cm["machineId"] == expected["machineId"]
    await es.close()


async def test_B3_1b_upsert_sets_retry_on_conflict():
    """Bug #4 regression: update must retry on version conflicts."""
    mock_es = MagicMock()
    mock_es.close = AsyncMock()
    mock_es.search = AsyncMock(return_value={"hits": {"hits": [_es_hit(doc_id="d1")]}})
    mock_es.update = AsyncMock(return_value={"result": "updated"})
    es = ElasticsearchService(client=mock_es)
    record = InferenceResultRecord.model_validate(
        {**VALID_INFERENCE_RESULT, "objStorePath": "p/x.png", "cropResults": []}
    )
    metadata = merge_inference_into_metadata(_board_metadata("p/x.png"), record)
    await es.upsert_inference_result(metadata)
    assert mock_es.update.await_args.kwargs["retry_on_conflict"] >= 1
    await es.close()


async def test_B3_2_upsert_not_found_returns_false():
    mock_es = MagicMock()
    mock_es.close = AsyncMock()
    mock_es.search = AsyncMock(return_value={"hits": {"hits": []}})
    es = ElasticsearchService(client=mock_es)
    assert await es.upsert_inference_result(_board_metadata("missing.png")) is False
    mock_es.update.assert_not_called()
    await es.close()


async def test_B3_3_search_term_uses_objstorepath_field():
    mock_es = MagicMock()
    mock_es.close = AsyncMock()
    mock_es.search = AsyncMock(return_value={"hits": {"hits": []}})
    es = ElasticsearchService(client=mock_es)
    await es.find_document_by_obj_store_path("p/x.png")
    mock_es.search.assert_awaited_once_with(
        index=settings.elasticsearch_index_pattern,
        query={"term": {OBJ_STORE_PATH_FIELD: "p/x.png"}},
        size=1,
    )
    await es.close()


async def test_B3_4_es_search_error_propagates():
    from elasticsearch import ApiError

    mock_es = MagicMock()
    mock_es.close = AsyncMock()
    mock_es.search = AsyncMock(
        side_effect=ApiError("search failed", meta=MagicMock(), body={})
    )
    es = ElasticsearchService(client=mock_es)
    metadata = _metadata_with_inference("p/x.png")
    with pytest.raises(ApiError):
        await es.upsert_inference_result(metadata)
    await es.close()


async def test_B3_5_es_update_error_propagates():
    from elasticsearch import ApiError

    mock_es = MagicMock()
    mock_es.close = AsyncMock()
    mock_es.search = AsyncMock(return_value={"hits": {"hits": [_es_hit(doc_id="d1")]}})
    mock_es.update = AsyncMock(
        side_effect=ApiError("update failed", meta=MagicMock(), body={})
    )
    es = ElasticsearchService(client=mock_es)
    metadata = _metadata_with_inference("p/x.png")
    with pytest.raises(ApiError):
        await es.upsert_inference_result(metadata)
    await es.close()


# ---- B4: endpoint POST /api/inference-results -----------------------------


def test_B4_1_valid_returns_200(client, mock_kafka):
    r = client.post("/api/inference-results", json=VALID_INFERENCE_RESULT)
    assert r.status_code == 200
    assert r.json() == {
        "objStorePath": VALID_INFERENCE_RESULT["objStorePath"],
        "updated": True,
    }
    mock_kafka.send.assert_awaited_once()


def test_B4_2_missing_metadata_returns_503(client, mock_storage):
    mock_storage.get_metadata = AsyncMock(side_effect=Exception("not found"))
    r = client.post("/api/inference-results", json=VALID_INFERENCE_RESULT)
    assert r.status_code == 503
    assert "could not publish inference" in r.json()["detail"]


def test_B4_3_kafka_exception_returns_503(client, mock_kafka):
    mock_kafka.send = AsyncMock(side_effect=RuntimeError("broker boom"))
    r = client.post("/api/inference-results", json=VALID_INFERENCE_RESULT)
    assert r.status_code == 503
    assert "failed to publish inference result" in r.json()["detail"]


def test_B4_4_invalid_body_returns_422(client):
    r = client.post("/api/inference-results", json={"objStorePath": "p"})
    assert r.status_code == 422


def test_B4_5_extra_field_returns_422(client):
    r = client.post(
        "/api/inference-results", json={**VALID_INFERENCE_RESULT, "extra": 1}
    )
    assert r.status_code == 422


# ==========================================================================
# Real-ES integration (gated on ES_E2E=1) — Bug #1 strict-mapping regression
# ==========================================================================

pytestmark_es = pytest.mark.skipif(
    os.getenv("ES_E2E") != "1", reason="set ES_E2E=1 to run against real ES"
)


@pytestmark_es
async def test_ES_completed_record_upserts_against_real_template():
    """End-to-end: a completed-crop record (old_model/new_model present) must
    upsert into a strict-mapped aoi_* index without strict_dynamic_mapping_exception."""
    es_url = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
    index = "aoi_scentest"
    path = "deft-aoi/scentest/board.png"
    async with httpx.AsyncClient(base_url=es_url, timeout=10.0) as raw:
        await raw.delete(f"/{index}")
        seed = {
            "text": "seed",
            "vector": [0.1] * 1024,
            "metadata": {
                "source": path,
                "content_metadata": {
                    "doc_type": "aoi_inspection",
                    "uuid": "u",
                    "doc_i": 0,
                    "chunkIdx": 0,
                    "machineId": "m",
                    "objStorePath": path,
                    "cropInfo": [
                        {
                            "id": "U7",
                            "label": "IC",
                            "crop": {
                                "cropType": "bbox",
                                "coordinates": {
                                    "left": 10,
                                    "top": 20,
                                    "width": 64,
                                    "height": 64,
                                },
                            },
                        }
                    ],
                },
            },
        }
        await raw.post(f"/{index}/_doc/scentest:1?refresh=true", json=seed)

    monkey_url = es_url
    from backend.config import settings as s

    s.es_url = monkey_url
    s.elasticsearch_index_pattern = "aoi_*"

    mock_storage = MagicMock()
    mock_storage.get_metadata = AsyncMock(
        return_value={
            **VALID_METADATA_WITH_CROPS,
            "objStorePath": path,
        }
    )
    es = ElasticsearchService()
    try:
        rec = InferenceResultRecord.model_validate(
            VALID_INFERENCE_RESULT | {"objStorePath": path}
        )
        raw = await mock_storage.get_metadata(path)
        metadata = merge_inference_into_metadata(_metadata_from_storage(raw), rec)
        assert await es.upsert_inference_result(metadata) is True
    finally:
        await es.close()


@pytestmark_es
async def test_ES_concurrent_upserts_same_doc_no_conflict():
    """Bug #4 regression (real ES): several concurrent results for the same board
    must all succeed via retry_on_conflict, not fail with 409/version conflict."""
    import asyncio

    es_url = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
    index = "aoi_conctest"
    path = "deft-aoi/conctest/board.png"
    async with httpx.AsyncClient(base_url=es_url, timeout=10.0) as raw:
        await raw.delete(f"/{index}")
        seed = {
            "text": "seed",
            "vector": [0.1] * 1024,
            "metadata": {
                "source": path,
                "content_metadata": {
                    "doc_type": "aoi_inspection",
                    "uuid": "c",
                    "doc_i": 0,
                    "chunkIdx": 0,
                    "machineId": "m",
                    "objStorePath": path,
                    "cropInfo": [
                        {
                            "id": "U7",
                            "label": "IC",
                            "crop": {
                                "cropType": "bbox",
                                "coordinates": {
                                    "left": 10,
                                    "top": 20,
                                    "width": 64,
                                    "height": 64,
                                },
                            },
                        }
                    ],
                },
            },
        }
        await raw.post(f"/{index}/_doc/conctest:1?refresh=true", json=seed)

    from backend.config import settings as s

    s.es_url = es_url
    s.elasticsearch_index_pattern = "aoi_*"
    rec = InferenceResultRecord.model_validate(
        VALID_INFERENCE_RESULT | {"objStorePath": path}
    )
    mock_storage = MagicMock()
    mock_storage.get_metadata = AsyncMock(
        return_value={
            **VALID_METADATA_WITH_CROPS,
            "objStorePath": path,
        }
    )
    es = ElasticsearchService()
    try:
        raw = await mock_storage.get_metadata(path)
        metadata = merge_inference_into_metadata(_metadata_from_storage(raw), rec)
        # 3 = realistic redelivery/retry overlap for one board; retry_on_conflict
        # absorbs the version conflicts so none surface as 409 -> 503.
        results = await asyncio.gather(
            *[es.upsert_inference_result(metadata) for _ in range(3)]
        )
        assert all(results)  # no 409 -> no exception -> all True
    finally:
        await es.close()
