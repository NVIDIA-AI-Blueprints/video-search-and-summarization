from client.api.router import get_admission
from client.application import app
from client.config import settings
from tests.helpers import BOARD_B64, make_request


class TestAccept:
    def test_valid_request_returns_202(self, client):
        resp = client.post("/api/inference", json=make_request())
        assert resp.status_code == 202
        body = resp.json()
        assert body["objStorePath"] == "deft-aoi/3f8a1b2c/photo1.jpg"
        assert body["status"] == "accepted"
        assert body["cropCount"] == 1

    def test_empty_crop_info_accepted_with_count_zero(self, client):
        resp = client.post("/api/inference", json=make_request(crop_info=[]))
        assert resp.status_code == 202
        assert resp.json()["cropCount"] == 0


class TestValidation:
    def test_invalid_base64_returns_422(self, client):
        resp = client.post("/api/inference", json=make_request(sample="not-b64!!!"))
        assert resp.status_code == 422

    def test_out_of_bounds_crop_returns_422(self, client):
        bad = [{"id": "U9", "crop": {"coordinates": {"left": 190, "top": 190, "width": 64, "height": 64}}}]
        resp = client.post("/api/inference", json=make_request(crop_info=bad))
        assert resp.status_code == 422
        assert "U9" in resp.text

    def test_non_image_bytes_returns_422(self, client):
        import base64

        junk = base64.b64encode(b"these-are-not-image-bytes").decode()
        resp = client.post("/api/inference", json=make_request(sample=junk))
        assert resp.status_code == 422

    def test_negative_coordinate_returns_422(self, client):
        bad = [{"id": "U9", "crop": {"coordinates": {"left": -1, "top": 0, "width": 10, "height": 10}}}]
        resp = client.post("/api/inference", json=make_request(crop_info=bad))
        assert resp.status_code == 422

    def test_missing_obj_store_path_returns_422(self, client):
        req = make_request()
        del req["metadata"]["objStorePath"]
        resp = client.post("/api/inference", json=req)
        assert resp.status_code == 422


class TestAdmission:
    def test_at_capacity_returns_503(self, client):
        from unittest.mock import AsyncMock, MagicMock

        full = MagicMock()
        full.try_admit = AsyncMock(return_value=False)
        full.release = AsyncMock()
        app.dependency_overrides[get_admission] = lambda: full
        try:
            resp = client.post("/api/inference", json=make_request())
            assert resp.status_code == 503
        finally:
            app.dependency_overrides.pop(get_admission, None)


class TestBodyLimit:
    def test_oversized_body_returns_413(self, client, monkeypatch):
        monkeypatch.setattr(settings, "max_request_bytes", 10)
        resp = client.post("/api/inference", json=make_request())
        assert resp.status_code == 413


class TestHealth:
    def test_health_ok(self, client):
        assert client.get("/health").json() == {"status": "ok"}

    def test_ready_ok(self, client):
        resp = client.get("/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    def test_ready_503_when_inference_down(self, client, mock_inference):
        mock_inference.ready.return_value = False
        resp = client.get("/ready")
        assert resp.status_code == 503
