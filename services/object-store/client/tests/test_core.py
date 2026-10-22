import io

from PIL import Image

from client.api.schemas import CropInfoItem
from client.core import aggregator, batcher, cropper
from client.core.batcher import Batch
from client.core.inference_client import BatchOutcome, CropScore, ModelScore, InferenceClient
from tests.helpers import BOARD_B64


def _crop_infos(specs):
    return [
        CropInfoItem.model_validate(
            {"id": cid, "crop": {"coordinates": {"left": l, "top": t, "width": w, "height": h}}}
        )
        for cid, (l, t, w, h) in specs
    ]


def _score(old_verdict="DEFECT", new_verdict="DEFECT") -> CropScore:
    return CropScore(
        old_model=ModelScore(version="1", score=0.9, verdict=old_verdict),
        new_model=ModelScore(version="2", score=0.8, verdict=new_verdict),
        score=0.9,
        verdict=old_verdict,
        threshold=0.33,
    )


class TestCropper:
    def test_crop_all_produces_one_pair_per_component(self):
        test_img = cropper.decode_board(BOARD_B64)
        golden_img = cropper.decode_board(BOARD_B64)
        infos = _crop_infos([("U1", (0, 0, 32, 32)), ("U2", (10, 10, 20, 20))])
        pairs = cropper.crop_all(test_img, golden_img, infos)
        assert [p[0] for p in pairs] == ["U1", "U2"]
        crop = Image.open(io.BytesIO(pairs[0][1]))
        assert crop.size == (32, 32)

    def test_validate_bounds_flags_out_of_range(self):
        test_img = cropper.decode_board(BOARD_B64)
        golden_img = cropper.decode_board(BOARD_B64)
        infos = _crop_infos([("U1", (180, 180, 64, 64))])  # exceeds 200x200
        errors = cropper.validate_bounds(test_img, golden_img, infos)
        assert len(errors) == 1
        assert "U1" in errors[0]

    def test_validate_bounds_ok_for_in_range(self):
        img = cropper.decode_board(BOARD_B64)
        infos = _crop_infos([("U1", (0, 0, 200, 200))])
        assert cropper.validate_bounds(img, img, infos) == []


class TestBatcher:
    def test_splits_into_batches_of_at_most_size(self):
        pairs = [(f"U{i}", b"t", b"g") for i in range(35)]
        batches = batcher.make_batches(pairs, 16)
        assert [len(b.crop_ids) for b in batches] == [16, 16, 3]
        assert [b.batch_number for b in batches] == [0, 1, 2]

    def test_empty(self):
        assert batcher.make_batches([], 16) == []


class TestAggregator:
    def _batch(self, ids):
        return Batch(0, ids, [b""] * len(ids), [b""] * len(ids))

    def test_all_completed(self):
        b = self._batch(["U1", "U2"])
        outcome = BatchOutcome(b, [_score("DEFECT"), _score("PASS", "PASS")])
        rec = aggregator.build_record("obj", [outcome])
        assert rec.status == "completed"
        assert rec.cropCount == 2 and rec.completedCount == 2 and rec.failedCount == 0
        result = rec.cropResults[0]
        assert result.old_model.verdict == "DEFECT"
        assert result.score == 0.9
        assert result.verdict == "DEFECT"
        assert result.threshold == 0.33

    def test_deployment_type_propagates_from_outcome(self):
        b = self._batch(["U1"])
        outcome = BatchOutcome(b, [_score()], deployment_type="Shadow")
        rec = aggregator.build_record("obj", [outcome])
        assert rec.deployment_type == "Shadow"

    def test_partial(self):
        ok = BatchOutcome(self._batch(["U1"]), [_score()])
        bad = BatchOutcome(self._batch(["U2"]), None, error="boom")
        rec = aggregator.build_record("obj", [ok, bad])
        assert rec.status == "partial"
        assert rec.completedCount == 1 and rec.failedCount == 1
        failed = [c for c in rec.cropResults if c.status == "failed"][0]
        assert failed.cropId == "U2" and failed.old_model is None

    def test_all_failed(self):
        bad = BatchOutcome(self._batch(["U1", "U2"]), None, error="boom")
        rec = aggregator.build_record("obj", [bad])
        assert rec.status == "failed"
        assert rec.completedCount == 0 and rec.failedCount == 2


class TestReplyMatching:
    def _client(self):
        import asyncio
        return InferenceClient(asyncio.Semaphore(1), client=object())

    def _item(self, index=0, score=0.9, verdict="DEFECT"):
        return {"index": index, "score": score, "verdict": verdict}

    def _valid_body(self, n=1):
        return {
            "deployment_type": "Shadow",
            "model_name": "test-model",
            "count": n,
            "threshold": 0.5,
            "results": [self._item(i) for i in range(n)],
            "old_model": {
                "model_name": "test-model",
                "version": "1",
                "results": [self._item(i, 0.9, "DEFECT") for i in range(n)],
            },
            "new_model": {
                "model_name": "test-model",
                "version": "2",
                "results": [self._item(i, 0.3, "PASS") for i in range(n)],
            },
        }

    def test_rejects_count_mismatch(self):
        c = self._client()
        batch = Batch(0, ["U1", "U2"], [b"", b""], [b"", b""])
        # body has 1 result but batch has 2 crops
        assert c._parse(self._valid_body(n=1), batch) is None

    def test_rejects_missing_results_key(self):
        c = self._client()
        batch = Batch(0, ["U1"], [b""], [b""])
        assert c._parse({"count": 1}, batch) is None

    def test_rejects_missing_old_model(self):
        c = self._client()
        batch = Batch(0, ["U1"], [b""], [b""])
        body = self._valid_body()
        del body["old_model"]
        assert c._parse(body, batch) is None

    def test_rejects_missing_new_model(self):
        c = self._client()
        batch = Batch(0, ["U1"], [b""], [b""])
        body = self._valid_body()
        del body["new_model"]
        assert c._parse(body, batch) is None

    def test_rejects_missing_threshold(self):
        c = self._client()
        batch = Batch(0, ["U1"], [b""], [b""])
        body = self._valid_body()
        del body["threshold"]
        assert c._parse(body, batch) is None

    def test_rejects_missing_score(self):
        c = self._client()
        batch = Batch(0, ["U1"], [b""], [b""])
        body = self._valid_body()
        del body["results"][0]["score"]
        assert c._parse(body, batch) is None

    def test_rejects_missing_verdict(self):
        c = self._client()
        batch = Batch(0, ["U1"], [b""], [b""])
        body = self._valid_body()
        del body["results"][0]["verdict"]
        assert c._parse(body, batch) is None

    def test_accepts_single_crop_reply(self):
        c = self._client()
        batch = Batch(0, ["U1"], [b""], [b""])
        result = c._parse(self._valid_body(), batch)
        assert result is not None
        scores, deployment_type, model_name = result
        assert len(scores) == 1
        assert scores[0].old_model.model_name == "test-model"
        assert scores[0].old_model.verdict == "DEFECT"
        assert scores[0].new_model.model_name == "test-model"
        assert scores[0].new_model.verdict == "PASS"
        assert scores[0].score == 0.9
        assert scores[0].threshold == 0.5
        assert deployment_type == "Shadow"
        assert model_name == "test-model"

    def test_accepts_multi_crop_reply(self):
        c = self._client()
        batch = Batch(0, ["U1", "U2"], [b"", b""], [b"", b""])
        body = self._valid_body(n=2)
        body["results"][1]["verdict"] = "PASS"
        body["results"][1]["score"] = 0.1
        result = c._parse(body, batch)
        assert result is not None
        scores, _, _ = result
        assert len(scores) == 2
        assert scores[0].verdict == "DEFECT"
        assert scores[1].verdict == "PASS"

    def test_rejects_missing_deployment_type(self):
        c = self._client()
        batch = Batch(0, ["U1"], [b""], [b""])
        body = self._valid_body()
        del body["deployment_type"]
        assert c._parse(body, batch) is None
