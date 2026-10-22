"""docker_backend — FIPS mask + command assembly.

The FIPS mask works around a HOST/image mismatch: on a FIPS-enabled host kernel
(`/proc/sys/crypto/fips_enabled=1`) the container inherits the flag and a stock
image's OpenSSL 3 dies trying to load an absent FIPS provider. Masking the flag
to 0 inside the container fixes it. It is opt-in via DEFT_FIPS_MASK and no-op on
non-FIPS hosts.

Run: uv run --with "pydantic>=2.6" pytest tests/test_docker_backend.py
"""

from __future__ import annotations

from app.execution import docker_backend as db

_MASK_TARGET = "/proc/sys/crypto/fips_enabled:ro"


def test_off_returns_empty_even_on_fips_host(monkeypatch):
    monkeypatch.setenv("DEFT_FIPS_MASK", "off")
    monkeypatch.setattr(db, "_host_fips_enabled", lambda: True)
    assert db._fips_mask_args() == []


def test_auto_non_fips_host_is_noop(monkeypatch):
    monkeypatch.setenv("DEFT_FIPS_MASK", "auto")
    monkeypatch.setattr(db, "_host_fips_enabled", lambda: False)
    assert db._fips_mask_args() == []


def test_auto_fips_host_masks(monkeypatch, tmp_path):
    zero = tmp_path / "deft_fips_zero"
    monkeypatch.setenv("DEFT_FIPS_MASK", "auto")
    monkeypatch.setattr(db, "_host_fips_enabled", lambda: True)
    monkeypatch.setattr(db, "_FIPS_ZERO_FILE", zero)
    args = db._fips_mask_args()
    assert args == ["-v", f"{zero}:{_MASK_TARGET}"]
    assert zero.read_text().strip() == "0"  # the mounted flag file reads 0


def test_on_forces_mask_even_without_fips(monkeypatch, tmp_path):
    zero = tmp_path / "z"
    monkeypatch.setenv("DEFT_FIPS_MASK", "on")
    monkeypatch.setattr(db, "_host_fips_enabled", lambda: False)
    monkeypatch.setattr(db, "_FIPS_ZERO_FILE", zero)
    args = db._fips_mask_args()
    assert args[0] == "-v" and args[1].endswith(_MASK_TARGET)


def test_docker_cmd_includes_mask_when_enabled(monkeypatch, tmp_path):
    zero = tmp_path / "z"
    monkeypatch.setenv("DEFT_FIPS_MASK", "on")
    monkeypatch.setattr(db, "_host_fips_enabled", lambda: False)
    monkeypatch.setattr(db, "_FIPS_ZERO_FILE", zero)
    spec = db.ContainerSpec(
        image="img", args=["run"], mounts={"/h": "/c"}, name="n",
        log_path=tmp_path / "log", use_gpu=False,
    )
    cmd = " ".join(db._docker_cmd(spec))
    assert _MASK_TARGET in cmd
    assert cmd.startswith("docker run --rm --name n --ipc=host")


def test_docker_cmd_no_mask_when_off(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFT_FIPS_MASK", "off")
    spec = db.ContainerSpec(
        image="img", args=["run"], mounts={}, name="n",
        log_path=tmp_path / "log", use_gpu=False,
    )
    assert _MASK_TARGET not in " ".join(db._docker_cmd(spec))
