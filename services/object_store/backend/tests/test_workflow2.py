import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.api.inference_schemas import InferenceResultRecord
from backend.api.inference_service import merge_inference_into_metadata
from backend.api.metadata import AOIMetadata
from backend.shared.aoi_client import AoiClientService
from backend.shared.elasticsearch import ElasticsearchService, OBJ_STORE_PATH_FIELD
from backend.tests.conftest import VALID_INFERENCE_RESULT, VALID_METADATA_WITH_CROPS, drain_aoi_client


def _board_metadata(path: str = "deft-aoi/abc/board.png") -> AOIMetadata:
    return AOIMetadata.model_validate(
        {**VALID_METADATA_WITH_CROPS, "objStorePath": path}
    )


def _metadata_with_inference(path: str = "deft-aoi/abc/board.png") -> AOIMetadata:
    record = InferenceResultRecord.model_validate(
        {**VALID_INFERENCE_RESULT, "objStorePath": path}
    )
    return merge_inference_into_metadata(_board_metadata(path), record)


@pytest.mark.asyncio
async def test_aoi_client_accepts_202():
    mock_http = MagicMock()
    mock_http.aclose = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 202
    mock_resp.json.return_value = {"cropCount": 2}
    mock_http.post = AsyncMock(return_value=mock_resp)
    client = AoiClientService(client=mock_http)
    client.schedule_trigger({"sampleImage": "x"}, "deft-aoi/abc/board.png")
    await drain_aoi_client(client)
    assert mock_http.post.await_count == 1
    await client.close()


@pytest.mark.asyncio
async def test_aoi_client_no_retry_on_422():
    mock_http = MagicMock()
    mock_http.aclose = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 422
    mock_resp.text = "validation error"
    mock_http.post = AsyncMock(return_value=mock_resp)
    client = AoiClientService(client=mock_http)
    client.schedule_trigger({"sampleImage": "x"}, "deft-aoi/abc/board.png")
    await drain_aoi_client(client)
    assert mock_http.post.await_count == 1
    await client.close()


@pytest.mark.asyncio
async def test_elasticsearch_upsert_finds_and_updates():
    mock_es = MagicMock()
    mock_es.close = AsyncMock()
    mock_es.search = AsyncMock(
        return_value={
            "hits": {
                "hits": [
                    {
                        "_index": "aoi_machine_1",
                        "_id": "doc-1",
                        "_source": {"metadata": {"content_metadata": {}}},
                    }
                ]
            }
        }
    )
    mock_es.update = AsyncMock(return_value={"result": "updated"})
    es = ElasticsearchService(client=mock_es)
    metadata = _metadata_with_inference()
    ok = await es.upsert_inference_result(metadata)
    assert ok is True
    search_call = mock_es.search.await_args
    assert OBJ_STORE_PATH_FIELD in search_call.kwargs["query"]["term"]
    update_doc = mock_es.update.await_args.kwargs["doc"]
    cm = update_doc["metadata"]["content_metadata"]
    assert cm["inferenceSummary"]["status"] == "completed"
    import hashlib
    expected_id = f"{hashlib.sha256('deft-aoi/abc/board.png'.encode()).hexdigest()[:32]}_U7"
    assert cm["cropInfo"][0]["inference"]["inspectionId"] == expected_id
    await es.close()


@pytest.mark.asyncio
async def test_elasticsearch_upsert_missing_doc():
    mock_es = MagicMock()
    mock_es.close = AsyncMock()
    mock_es.search = AsyncMock(return_value={"hits": {"hits": []}})
    es = ElasticsearchService(client=mock_es)
    metadata = _board_metadata("missing/path.png")
    ok = await es.upsert_inference_result(metadata)
    assert ok is False
    await es.close()
