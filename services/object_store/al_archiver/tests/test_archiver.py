import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from al_archiver.core.consumer import ArchiverConsumer
from al_archiver.core.forwarder import Forwarder
from tests.conftest import SAMPLE_RECORD


def _response(status_code, text=""):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    return resp


class TestForwarder:
    async def test_forward_success(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=_response(200))
        fwd = Forwarder(client=client)
        assert await fwd.forward(SAMPLE_RECORD) is True
        client.post.assert_awaited_once()
        assert client.post.await_args.kwargs["json"] == SAMPLE_RECORD

    async def test_non_retryable_status_returns_false_without_retry(self):
        client = MagicMock()
        client.post = AsyncMock(return_value=_response(400, "bad"))
        fwd = Forwarder(client=client)
        assert await fwd.forward(SAMPLE_RECORD) is False
        assert client.post.await_count == 1

    async def test_retries_then_succeeds(self, monkeypatch):
        from al_archiver.core import forwarder

        monkeypatch.setattr(forwarder.asyncio, "sleep", AsyncMock())
        client = MagicMock()
        client.post = AsyncMock(side_effect=[_response(503), _response(200)])
        fwd = Forwarder(client=client)
        assert await fwd.forward(SAMPLE_RECORD) is True
        assert client.post.await_count == 2

    async def test_gives_up_after_max_retries(self, monkeypatch):
        from al_archiver.core import forwarder

        monkeypatch.setattr(forwarder.asyncio, "sleep", AsyncMock())
        monkeypatch.setattr(forwarder.settings, "max_retries", 2)
        client = MagicMock()
        client.post = AsyncMock(side_effect=httpx.ConnectError("down"))
        fwd = Forwarder(client=client)
        assert await fwd.forward(SAMPLE_RECORD) is False
        assert client.post.await_count == 3  # initial + 2 retries


class TestConsumerHandling:
    async def test_handle_forwards_decoded_record(self, mock_forwarder):
        consumer = ArchiverConsumer(mock_forwarder, consumer=MagicMock())
        msg = MagicMock()
        msg.value = json.dumps(SAMPLE_RECORD).encode()
        await consumer._handle(msg)
        mock_forwarder.forward.assert_awaited_once_with(SAMPLE_RECORD)

    async def test_handle_skips_undecodable(self, mock_forwarder):
        consumer = ArchiverConsumer(mock_forwarder, consumer=MagicMock())
        msg = MagicMock()
        msg.value = b"not-json{{{"
        msg.offset = 7
        await consumer._handle(msg)
        mock_forwarder.forward.assert_not_awaited()


class TestApp:
    @pytest.fixture
    def client(self, mock_forwarder, mock_consumer):
        from al_archiver.application import app

        with (
            patch("al_archiver.application.Forwarder", return_value=mock_forwarder),
            patch("al_archiver.application.ArchiverConsumer", return_value=mock_consumer),
        ):
            with TestClient(app) as c:
                yield c

    def test_health(self, client):
        assert client.get("/health").json() == {"status": "ok"}

    def test_ready(self, client):
        assert client.get("/ready").status_code == 200

    def test_not_ready(self, client, mock_consumer):
        mock_consumer.is_ready = False
        assert client.get("/ready").status_code == 503
