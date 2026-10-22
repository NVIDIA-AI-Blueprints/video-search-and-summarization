"""Tests for the AOI snapshot -> ChangeNet training-pool converter.

Stdlib + Pillow. Verifies the converted pool satisfies the workspace contract
(required columns, no train/val leakage, >=1 defect in train, both classes in
kpi, images on disk) that validate_training_pool enforces, plus golden-ref
resolution, the fetch fallback, and the fail-fast error paths.

Run: uv run --with pillow pytest tests/test_build_pool.py
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app import config
from app.execution.build_pool import (
    PoolBuildError,
    _label,
    build_training_pool,
    pool_needs_build,
)
from app.execution.workspace import (
    REQUIRED_COLUMNS,
    Workspace,
    leakage_pairs,
    normalize_label,
    read_rows,
)

Image = pytest.importorskip("PIL.Image")


@pytest.fixture(autouse=True)
def _isolate_backbone_fetch(monkeypatch, tmp_path):
    # Unit tests must never hit the network or a real ~/.cache backbone: disable
    # the HF backbone download and point the cache at a nonexistent path.
    monkeypatch.setenv("DEFT_BACKBONE_URL", "")
    monkeypatch.setenv("DEFT_BACKBONE_CACHE", str(tmp_path / "no_backbone_cache.safetensors"))


def _board_png(size=(240, 240), color=(120, 90, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _crop_entry(comp_id: str, box, defect: str | None):
    left, top, width, height = box
    return {
        "id": comp_id,
        "aoiDefectInfo": {"defectType": defect} if defect else {"defectType": ""},
        "crop": {"coordinates": {"left": left, "top": top, "width": width, "height": height},
                 "cropType": "bbox"},
    }


def _make_ws(tmp_path: Path, *, boards=2, per_board=6, defect_every=2,
             board_size=(240, 240), write_image=True, golden_ref=False,
             bad_box=False) -> Path:
    """A materialized-snapshot workspace: manifest*.jsonl + board PNGs on disk."""
    ws = tmp_path / "ws"
    ws.mkdir()
    lines = []
    for b in range(boards):
        key = f"deft-aoi/hash{b:02d}/board{b}.png"
        if write_image:
            img_path = ws / key
            img_path.parent.mkdir(parents=True, exist_ok=True)
            img_path.write_bytes(_board_png(board_size, color=(100 + b * 5, 90, 60)))
        crops = []
        for c in range(per_board):
            left = 10 + (c % 4) * 50
            top = 10 + (c // 4) * 50
            box = (10_000, 10_000, 40, 40) if bad_box else (left, top, 40, 40)
            defect = "Solder Bridge" if (defect_every and c % defect_every == 0) else None
            crops.append(_crop_entry(f"U{c}", box, defect))
        meta = {"objStorePath": key, "cropInfo": crops}
        if golden_ref:
            gkey = f"deft-aoi/hash{b:02d}/golden{b}.png"
            (ws / gkey).parent.mkdir(parents=True, exist_ok=True)
            (ws / gkey).write_bytes(_board_png(board_size, color=(10, 200, 10)))
            meta["golden-ref"] = gkey
        lines.append(json.dumps({"id": f"rec{b}", "file_path": key, "metadata": meta}))
    (ws / "manifest0.jsonl").write_text("\n".join(lines) + "\n")
    return ws


def _ws_with_records(tmp_path: Path, records: list[dict], images: dict[str, bytes]) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    for key, data in images.items():
        (ws / key).parent.mkdir(parents=True, exist_ok=True)
        (ws / key).write_bytes(data)
    (ws / "manifest0.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return ws


def _all_object_names(ws: Path) -> list[str]:
    names = []
    for rel in ("train/base/training_set.csv", "train/base/validation_set.csv",
                "kpi/testing_set.csv", "augmentation/mining_pool/mining_pool.csv"):
        names += [r["object_name"] for r in read_rows(ws / rel)[1]]
    return names


def _kpi_ws(tmp_path: Path, name: str, *, boards=2, per_board=4, all_pass=False) -> Path:
    """A separate manifest-form KPI set (its own boards + defect.xml-style crops)."""
    d = tmp_path / name
    d.mkdir()
    lines = []
    for b in range(boards):
        key = f"deft-aoi/kpi{b:02d}/board.png"
        (d / key).parent.mkdir(parents=True, exist_ok=True)
        (d / key).write_bytes(_board_png(color=(10, 10, 180 + b * 10)))
        crops = [_crop_entry(f"K{c}", (10 + (c % 4) * 50, 10 + (c // 4) * 50, 40, 40),
                             None if all_pass else ("Solder Bridge" if c % 2 == 0 else None))
                 for c in range(per_board)]
        lines.append(json.dumps({"id": f"k{b}", "file_path": key,
                                 "metadata": {"objStorePath": key, "cropInfo": crops}}))
    (d / "manifest0.jsonl").write_text("\n".join(lines) + "\n")
    return d


# --------------------------------------------------------------------------- #

def test_pool_needs_build_gate(tmp_path):
    ws = _make_ws(tmp_path)
    assert pool_needs_build(ws) is True
    (ws / "train/base").mkdir(parents=True)
    (ws / "train/base/training_set.csv").write_text("input_path\n")
    assert pool_needs_build(ws) is False
    assert pool_needs_build(tmp_path / "empty") is False


def test_builds_full_changenet_schema(tmp_path):
    ws = _make_ws(tmp_path, boards=2, per_board=6, defect_every=2)
    build_training_pool(ws, batch_size=2)

    train = ws / "train/base/training_set.csv"
    val = ws / "train/base/validation_set.csv"
    kpi = ws / "kpi/testing_set.csv"
    pool = ws / "augmentation/mining_pool/mining_pool.csv"
    for p in (train, val, kpi, pool):
        assert p.is_file(), f"missing {p}"

    fields, train_rows = read_rows(train)
    for col in REQUIRED_COLUMNS:
        assert col in fields
    assert any(normalize_label(r["label"]) != "PASS" for r in train_rows)
    assert len(train_rows) >= 2
    _, val_rows = read_rows(val)
    assert len(val_rows) >= 2
    assert leakage_pairs(train_rows, val_rows) == 0
    _, kpi_rows = read_rows(kpi)
    labels = {normalize_label(r["label"]) for r in kpi_rows}
    assert "PASS" in labels and any(l != "PASS" for l in labels)


def test_crop_images_exist_on_disk(tmp_path):
    ws = _make_ws(tmp_path, boards=2, per_board=6)
    build_training_pool(ws, batch_size=2)
    _, rows = read_rows(ws / "train/base/training_set.csv")
    imgs = ws / "kpi/images"
    for r in rows:
        for col in ("input_path", "golden_path"):
            d = imgs / r[col]
            assert d.is_dir()
            assert (d / f"{r['object_name']}_SolderLight.jpg").is_file()
    _, pool_rows = read_rows(ws / "augmentation/mining_pool/mining_pool.csv")
    for r in pool_rows:
        assert (ws / "augmentation/mining_pool" / r["input_path"]).is_file()


def test_golden_ref_board_used_when_present(tmp_path):
    ws = _make_ws(tmp_path, boards=2, per_board=6, golden_ref=True)
    build_training_pool(ws, batch_size=2)
    imgs = ws / "kpi/images"
    _, rows = read_rows(ws / "train/base/training_set.csv")
    r = rows[0]
    test_img = Image.open(imgs / r["input_path"] / f"{r['object_name']}_SolderLight.jpg")
    golden_img = Image.open(imgs / r["golden_path"] / f"{r['object_name']}_SolderLight.jpg")
    assert test_img.tobytes() != golden_img.tobytes()


# The AOI upload API takes the wire schema (kebab-case, nested under imageInfo),
# but every manifest record carries the storage schema the ingest pipeline writes
# to Elasticsearch (camelCase, imageInfo flattened away). _golden_key sees only
# manifest records, so the storage spelling is the one that matters in practice.

def test_golden_key_resolves_storage_schema_camel_case():
    import app.execution.build_pool as bp

    assert bp._golden_key({"goldenRef": "deft-aoi/07e9ae80/U102.jpg"}) == "deft-aoi/07e9ae80/U102.jpg"
    assert bp._golden_key({"imageInfo": {"goldenRef": "deft-aoi/07e9ae80/U102.jpg"}}) \
        == "deft-aoi/07e9ae80/U102.jpg"
    assert bp._golden_key({"goldenRef": "  deft-aoi/h0/golden.png  "}) == "deft-aoi/h0/golden.png"


def test_golden_key_still_resolves_wire_schema_kebab_case():
    import app.execution.build_pool as bp

    assert bp._golden_key({"golden-ref": "deft-aoi/h0/golden.png"}) == "deft-aoi/h0/golden.png"
    assert bp._golden_key({"imageInfo": {"golden-ref": "deft-aoi/h0/golden.png"}}) \
        == "deft-aoi/h0/golden.png"


def test_golden_key_none_when_absent_or_blank():
    import app.execution.build_pool as bp

    assert bp._golden_key({}) is None
    assert bp._golden_key({"objStorePath": "deft-aoi/h0/board.png", "cropInfo": []}) is None
    assert bp._golden_key({"imageInfo": None}) is None
    assert bp._golden_key({"goldenRef": "", "golden-ref": "   "}) is None
    assert bp._golden_key({"imageInfo": {"goldenRef": None, "golden-ref": ""}}) is None


def test_storage_schema_golden_ref_leaves_no_sample_as_golden(tmp_path):
    # A record shaped the way a frozen manifest actually is: content_metadata
    # flattened, golden reference spelled goldenRef. Every crop must pair with the
    # real golden board, so nothing may fall back to sample-as-golden.
    import app.execution.build_pool as bp

    key, gkey = "deft-aoi/h0/board.png", "deft-aoi/h0/golden.png"
    crops = [_crop_entry(f"U{c}", (10 + (c % 4) * 50, 10 + (c // 4) * 50, 40, 40),
                         "Solder Bridge" if c % 2 == 0 else None) for c in range(8)]
    ws = _ws_with_records(
        tmp_path,
        [{"id": "rec0", "file_path": key,
          "metadata": {"objStorePath": key, "goldenRef": gkey, "cropInfo": crops}}],
        {key: _board_png(color=(120, 90, 60)), gkey: _board_png(color=(10, 200, 10))},
    )
    objs, stats = bp._convert_manifests(ws, ws / "kpi/images")
    assert len(objs) == 8
    assert stats["sample_golden"] == 0
    obj = objs[0]
    imgs = ws / "kpi/images"
    test_img = Image.open(imgs / obj.input_dir / f"{obj.object_name}_SolderLight.jpg")
    golden_img = Image.open(imgs / obj.golden_dir / f"{obj.object_name}_SolderLight.jpg")
    assert test_img.tobytes() != golden_img.tobytes()


def test_fetch_image_fallback_when_not_local(tmp_path):
    ws = _make_ws(tmp_path, boards=2, per_board=6, write_image=False)
    served = {}

    def fetch(key: str) -> bytes:
        served[key] = served.get(key, 0) + 1
        return _board_png()

    build_training_pool(ws, batch_size=2, fetch_image=fetch)
    assert served
    assert (ws / "train/base/training_set.csv").is_file()


def test_missing_board_without_fetch_raises(tmp_path):
    ws = _make_ws(tmp_path, boards=2, per_board=6, write_image=False)
    with pytest.raises(PoolBuildError):
        build_training_pool(ws, batch_size=2)


def test_out_of_bounds_crops_skipped(tmp_path):
    ws = _make_ws(tmp_path, boards=2, per_board=6, bad_box=True)
    with pytest.raises(PoolBuildError, match="no usable components"):
        build_training_pool(ws, batch_size=2)


def test_no_defect_components_raises(tmp_path):
    ws = _make_ws(tmp_path, boards=2, per_board=6, defect_every=0)  # all PASS
    with pytest.raises(PoolBuildError, match="no defect"):
        build_training_pool(ws, batch_size=2)


def test_insufficient_objects_raises(tmp_path):
    ws = _make_ws(tmp_path, boards=1, per_board=3)
    with pytest.raises(PoolBuildError, match="insufficient"):
        build_training_pool(ws, batch_size=4)


def test_board_id_unique_across_same_filename_boards(tmp_path):
    # two boards share a filename and carry NO record id -> must not collide.
    recs, images = [], {}
    for b in range(2):
        key = f"deft-aoi/h{b}/board.png"  # SAME basename, different dir, no id
        images[key] = _board_png(color=(40 + b * 60, 20, 20))
        crops = [_crop_entry(f"U{c}", (10 + (c % 4) * 50, 10 + (c // 4) * 50, 40, 40),
                             "Solder Bridge" if c % 2 == 0 else None) for c in range(6)]
        recs.append({"file_path": key, "metadata": {"objStorePath": key, "cropInfo": crops}})
    ws = _ws_with_records(tmp_path, recs, images)
    build_training_pool(ws, batch_size=2)
    names = _all_object_names(ws)
    assert len(names) == len(set(names)) == 12  # 2 boards x 6 comps, zero collision


def test_skewed_single_defect_insufficient_for_train(tmp_path):
    # 1 defect among many pass: KPI claims the defect, train can't get one -> raise.
    key = "deft-aoi/h/b.png"
    crops = [_crop_entry("D0", (10, 10, 40, 40), "Solder Bridge")]
    crops += [_crop_entry(f"P{i}", (10 + (i % 4) * 50, 120, 30, 20), None) for i in range(12)]
    ws = _ws_with_records(tmp_path, [{"file_path": key, "metadata": {"objStorePath": key, "cropInfo": crops}}],
                          {key: _board_png()})
    with pytest.raises(PoolBuildError, match="insufficient"):
        build_training_pool(ws, batch_size=2)


def test_skewed_enough_defects_kpi_has_both_classes(tmp_path):
    # 3 defects + many pass, batch=2: valid, and KPI carries both classes.
    key = "deft-aoi/h/b.png"
    crops = [_crop_entry(f"D{i}", (10 + (i % 4) * 50, 10, 40, 40), "Solder Bridge") for i in range(3)]
    crops += [_crop_entry(f"P{i}", (10 + (i % 4) * 50, 120, 30, 20), None) for i in range(12)]
    ws = _ws_with_records(tmp_path, [{"file_path": key, "metadata": {"objStorePath": key, "cropInfo": crops}}],
                          {key: _board_png()})
    build_training_pool(ws, batch_size=2)
    labels = {normalize_label(r["label"]) for r in read_rows(ws / "kpi/testing_set.csv")[1]}
    assert "PASS" in labels and any(l != "PASS" for l in labels)


def test_malformed_coords_skipped_not_crashed(tmp_path):
    # null coords -> skipped; numeric-string coords -> used (no crash).
    key = "deft-aoi/h/b.png"
    crops = [{"id": "BAD", "aoiDefectInfo": {"defectType": "Solder Bridge"},
              "crop": {"coordinates": {"left": None, "top": 10, "width": 40, "height": 40}}}]
    crops += [_crop_entry(f"D{i}", ("12.5", 10, "40", 40), "Solder Bridge") for i in range(3)]
    crops += [_crop_entry(f"P{i}", (10 + (i % 4) * 50, 120, 30, 20), None) for i in range(12)]
    ws = _ws_with_records(tmp_path, [{"file_path": key, "metadata": {"objStorePath": key, "cropInfo": crops}}],
                          {key: _board_png()})
    build_training_pool(ws, batch_size=2)  # BAD skipped, numeric strings used -> succeeds
    assert not any("BAD" in n for n in _all_object_names(ws))


def test_leading_slash_objstorepath_resolves(tmp_path):
    # objStorePath with a leading slash must still resolve under the workspace.
    key = "/deft-aoi/h/b.png"
    crops = [_crop_entry(f"U{c}", (10 + (c % 4) * 50, 10 + (c // 4) * 50, 40, 40),
                         "Solder Bridge" if c % 2 == 0 else None) for c in range(8)]
    ws = _ws_with_records(tmp_path, [{"file_path": key, "metadata": {"objStorePath": key, "cropInfo": crops}}],
                          {"deft-aoi/h/b.png": _board_png()})  # stored without leading slash
    build_training_pool(ws, batch_size=2)
    assert (ws / "train/base/training_set.csv").is_file()


def test_unreadable_board_skipped_not_crashed(tmp_path):
    # one board image is corrupt (not a valid image) -> that board is skipped,
    # the rest still build (a bad board must not fail the whole run).
    good = _make_ws(tmp_path, boards=2, per_board=8)
    # append a third record whose image bytes are garbage.
    bad_key = "deft-aoi/bad/board.png"
    (good / bad_key).parent.mkdir(parents=True, exist_ok=True)
    (good / bad_key).write_bytes(b"not-an-image")
    crops = [_crop_entry(f"U{c}", (10, 10, 40, 40), "Solder Bridge") for c in range(3)]
    with open(good / "manifest0.jsonl", "a") as fh:
        fh.write(json.dumps({"id": "bad", "file_path": bad_key,
                             "metadata": {"objStorePath": bad_key, "cropInfo": crops}}) + "\n")
    build_training_pool(good, batch_size=2)  # succeeds despite the corrupt board
    assert not any("bad" in n for n in _all_object_names(good))


def test_external_kpi_set_becomes_the_test_split(tmp_path):
    # A separate KPI manifest set supplies kpi/testing_set.csv; train/val/mining
    # come from the training snapshot (kpi is NOT carved from it).
    ws = _make_ws(tmp_path, boards=2, per_board=8)          # training snapshot
    kpi_src = _kpi_ws(tmp_path, "kpi_source", boards=2, per_board=4)  # 8 kpi comps
    build_training_pool(ws, kpi_source=kpi_src, batch_size=2)

    _, kpi_rows = read_rows(ws / "kpi/testing_set.csv")
    assert kpi_rows and all("kpiset_" in r["object_name"] for r in kpi_rows)
    labels = {normalize_label(r["label"]) for r in kpi_rows}
    assert "PASS" in labels and any(l != "PASS" for l in labels)  # both classes

    _, tr = read_rows(ws / "train/base/training_set.csv")
    _, va = read_rows(ws / "train/base/validation_set.csv")
    assert tr and not any("kpiset_" in r["object_name"] for r in tr + va)  # from snapshot
    # kpi and train are disjoint (no leakage across sources).
    assert not ({r["object_name"] for r in kpi_rows} & {r["object_name"] for r in tr})
    # the kpi crops exist on disk.
    for r in kpi_rows:
        d = ws / "kpi/images" / r["input_path"]
        assert (d / f"{r['object_name']}_SolderLight.jpg").is_file()


def test_external_kpi_set_requires_both_classes(tmp_path):
    ws = _make_ws(tmp_path, boards=2, per_board=8)
    kpi_src = _kpi_ws(tmp_path, "kpi_source", boards=2, per_board=4, all_pass=True)
    with pytest.raises(PoolBuildError, match="KPI set needs both"):
        build_training_pool(ws, kpi_source=kpi_src, batch_size=2)


def test_bundled_specs_staged_without_template_dir(tmp_path, monkeypatch):
    # zero external setup: with no DEFT_POOL_TEMPLATE_DIR, DEFT stages its BUNDLED
    # default specs so `profile: aoi_default` runs out of the box.
    monkeypatch.delenv("DEFT_POOL_TEMPLATE_DIR", raising=False)
    ws = _make_ws(tmp_path, boards=2, per_board=8)
    build_training_pool(ws, batch_size=2)
    assert (ws / "specs/baseline_spec.yaml").is_file()
    assert (ws / "specs/baseline_inference_spec.yaml").is_file()


def test_backbone_fetched_and_staged_under_canonical_name(tmp_path, monkeypatch):
    # no template dir + no workspace backbone -> the HF-fetched backbone is staged
    # under the canonical model.safetensors the results/publish contract requires,
    # NOT its cache filename.
    import app.execution.build_pool as bp

    monkeypatch.delenv("DEFT_POOL_TEMPLATE_DIR", raising=False)
    fake = tmp_path / "cache" / "c_radio_v2_b.safetensors"  # cache name != contract name
    fake.parent.mkdir(parents=True)
    fake.write_bytes(b"weights")
    monkeypatch.setattr(bp, "_fetch_backbone", lambda: fake)
    ws = _make_ws(tmp_path, boards=2, per_board=8)
    build_training_pool(ws, batch_size=2)
    assert (ws / "augmentation/backbone/model.safetensors").is_file()
    assert not (ws / "augmentation/backbone/c_radio_v2_b.safetensors").exists()


def test_backbone_fetch_uses_cache_without_download(tmp_path, monkeypatch):
    import app.execution.build_pool as bp

    cache = tmp_path / "c_radio_v2_b.safetensors"
    cache.write_bytes(b"cached")
    monkeypatch.setenv("DEFT_BACKBONE_CACHE", str(cache))
    monkeypatch.setenv("DEFT_BACKBONE_URL", "http://example.invalid/must-not-be-hit")
    assert bp._fetch_backbone() == cache  # cache hit -> no network


def test_backbone_fetch_disabled_returns_none(tmp_path, monkeypatch):
    import app.execution.build_pool as bp

    monkeypatch.setenv("DEFT_BACKBONE_URL", "")  # download disabled
    monkeypatch.setenv("DEFT_BACKBONE_CACHE", str(tmp_path / "absent.safetensors"))
    assert bp._fetch_backbone() is None


def _fake_urlopen_factory(fail_times, http_code, payload):
    import io
    import urllib.error

    calls = {"n": 0}

    class _Resp(io.BytesIO):
        def __init__(self, data):
            super().__init__(data)
            self.headers = {"Content-Length": str(len(data))}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise urllib.error.HTTPError(
                req.full_url, http_code, "transient", {"Retry-After": "0"}, None
            )
        return _Resp(payload)

    return fake_urlopen, calls


def test_backbone_fetch_retries_transient_then_succeeds(tmp_path, monkeypatch):
    import urllib.request

    import app.execution.build_pool as bp

    cache = tmp_path / "bb.safetensors"
    payload = b"B" * (1 << 20)  # >= 1 MiB integrity floor, Content-Length matches
    monkeypatch.setenv("DEFT_BACKBONE_CACHE", str(cache))
    monkeypatch.setenv("DEFT_BACKBONE_URL", "http://mirror.invalid/m.safetensors")
    monkeypatch.setenv("DEFT_BACKBONE_RETRIES", "3")
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    fake, calls = _fake_urlopen_factory(fail_times=2, http_code=429, payload=payload)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert bp._fetch_backbone() == cache
    assert cache.read_bytes() == payload
    assert calls["n"] == 3  # two 429s + one success
    assert not list(cache.parent.glob(".*.part"))  # per-process temp cleaned/promoted


def test_backbone_fetch_rejects_truncated_download(tmp_path, monkeypatch):
    # a short body (server Content-Length says more, or an error page) must NOT be
    # promoted into the cache.
    import urllib.request

    import app.execution.build_pool as bp

    cache = tmp_path / "bb.safetensors"
    monkeypatch.setenv("DEFT_BACKBONE_CACHE", str(cache))
    monkeypatch.setenv("DEFT_BACKBONE_URL", "http://mirror.invalid/m.safetensors")
    monkeypatch.setenv("DEFT_BACKBONE_RETRIES", "0")
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    fake, _ = _fake_urlopen_factory(fail_times=0, http_code=200, payload=b"<html>429</html>")
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert bp._fetch_backbone() is None  # too small -> rejected, not cached
    assert not cache.exists()


def test_backbone_fetch_gives_up_after_retries(tmp_path, monkeypatch):
    import urllib.request

    import app.execution.build_pool as bp

    cache = tmp_path / "bb.safetensors"
    monkeypatch.setenv("DEFT_BACKBONE_CACHE", str(cache))
    monkeypatch.setenv("DEFT_BACKBONE_URL", "http://mirror.invalid/m.safetensors")
    monkeypatch.setenv("DEFT_BACKBONE_RETRIES", "2")
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    fake, calls = _fake_urlopen_factory(fail_times=99, http_code=429, payload=b"")
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert bp._fetch_backbone() is None
    assert calls["n"] == 3  # initial + 2 retries, then give up
    assert not cache.exists()


def test_spec_falls_back_to_bundled_when_template_missing_one(tmp_path, monkeypatch):
    # template dir supplies only the TRAINING spec; the inference spec must still be
    # staged from the bundled default (per-file fallback, no all-or-nothing gap).
    tpl = tmp_path / "tpl"
    (tpl / "specs").mkdir(parents=True)
    (tpl / "specs/baseline_spec.yaml").write_text("train: custom\n")
    (tpl / "backbone").mkdir()
    (tpl / "backbone/model.safetensors").write_bytes(b"weights")
    monkeypatch.setenv("DEFT_POOL_TEMPLATE_DIR", str(tpl))
    ws = _make_ws(tmp_path, boards=2, per_board=8)
    build_training_pool(ws, batch_size=2)
    assert (ws / "specs/baseline_spec.yaml").read_text() == "train: custom\n"  # template wins
    assert (ws / "specs/baseline_inference_spec.yaml").is_file()  # bundled fallback


def test_templates_staged_when_configured(tmp_path, monkeypatch):
    tpl = tmp_path / "tpl"
    (tpl / "specs").mkdir(parents=True)
    (tpl / "specs/baseline_spec.yaml").write_text("a: 1\n")
    (tpl / "specs/baseline_inference_spec.yaml").write_text("b: 2\n")
    (tpl / "backbone").mkdir()
    (tpl / "backbone/model.safetensors").write_bytes(b"weights")
    monkeypatch.setenv("DEFT_POOL_TEMPLATE_DIR", str(tpl))

    ws = _make_ws(tmp_path, boards=2, per_board=6)
    build_training_pool(ws, batch_size=2)
    assert (ws / "specs/baseline_spec.yaml").is_file()
    assert (ws / "specs/baseline_inference_spec.yaml").is_file()
    assert (ws / "augmentation/backbone/model.safetensors").is_file()


# ---------------------------------------------------------------------------
# confirmed_label — the AOI station's confirmation is authoritative on PASS/DEFECT
# ---------------------------------------------------------------------------

def _crop_with(confirmed, defect, key="confirmed_label"):
    crop = _crop_entry("U0", (10, 10, 40, 40), defect)
    if confirmed is not None:
        crop[key] = confirmed
    return crop


def test_confirmed_pass_overrides_a_flagged_defect():
    """The false-alarm case: station flagged it, confirmation says it is good.

    Labelling this by defectType would train the model to reproduce exactly the
    false alarm the FAR gate penalizes.
    """
    assert _label(_crop_with("PASS", "mouse bite")) == "PASS"


def test_confirmed_defect_takes_its_subtype_from_the_station():
    assert _label(_crop_with("DEFECT", "Mouse Bite")) == "mouse bite"


def test_confirmed_defect_without_a_subtype_stays_trainable():
    assert _label(_crop_with("DEFECT", None)) == "defect"
    assert _label(_crop_with("DEFECT", "PASS")) == "defect"


def test_confirmation_is_read_case_insensitively_and_from_either_schema():
    assert _label(_crop_with("pass", "spur")) == "PASS"
    assert _label(_crop_with("PASS", "spur", key="confirmedLabel")) == "PASS"


def test_unconfirmed_falls_back_to_the_station_by_default(monkeypatch):
    monkeypatch.setattr(config, "LABEL_POLICY", "confirmed_else_machine")
    assert _label(_crop_with(None, "spur")) == "spur"
    assert _label(_crop_with(None, None)) == "PASS"
    assert _label(_crop_with("", "spur")) == "spur"


def test_unconfirmed_is_excluded_under_confirmed_only(monkeypatch):
    monkeypatch.setattr(config, "LABEL_POLICY", "confirmed_only")
    assert _label(_crop_with(None, "spur")) is None
    assert _label(_crop_with(None, None)) is None
    # A confirmed component is still labelled under the strict policy.
    assert _label(_crop_with("PASS", "spur")) == "PASS"
    assert _label(_crop_with("DEFECT", "spur")) == "spur"


def test_confirmed_pass_reaches_the_built_pool_as_pass(tmp_path):
    """End-to-end through build_training_pool, not just the helper."""
    ws = _make_ws(tmp_path, boards=4, per_board=6, defect_every=2)
    # Confirm every station-flagged component as good on one board, leaving the
    # rest untouched, then assert none of those rows carry the defect class.
    mf = ws / "manifest0.jsonl"
    lines = [json.loads(x) for x in mf.read_text().splitlines()]
    n_flagged = 0
    for crop in lines[0]["metadata"]["cropInfo"]:
        if (crop.get("aoiDefectInfo") or {}).get("defectType"):
            crop["confirmed_label"] = "PASS"
            n_flagged += 1
    assert n_flagged, "fixture must flag at least one component to overrule"
    mf.write_text("".join(json.dumps(x) + "\n" for x in lines))

    build_training_pool(ws, batch_size=1)

    rows = []
    for csv_name in ("train/base/training_set.csv", "train/base/validation_set.csv",
                     "kpi/testing_set.csv", "mining/mining_pool.csv"):
        p = ws / csv_name
        if p.is_file():
            rows.extend(read_rows(p)[1])

    # Board 0 had every flagged component confirmed PASS, so nothing from it may
    # carry a defect label. Other boards are untouched and still supply defects.
    board0 = [r for r in rows if r["boardname"] == "board0_0000"]
    assert board0, "board 0's components should still be in the pool"
    assert all(normalize_label(r["label"]) == "PASS" for r in board0), \
        f"confirmed-PASS must override defectType: {sorted({r['label'] for r in board0})}"
    assert any(normalize_label(r["label"]) != "PASS" for r in rows), \
        "other boards should still contribute defects"
