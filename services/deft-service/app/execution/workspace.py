"""AOI workspace resolution + ChangeNet CSV utilities.

The AOI real-execution path operates on a DEFT workspace laid out per the
skill bank's Data Contract (tao-run-deft-aoi):

    <workspace>/
      specs/            baseline_spec.yaml, baseline_inference_spec.yaml
      train/base/       training_set.csv, validation_set.csv   (paths relative to kpi/images)
      kpi/              testing_set.csv + images/
      augmentation/mining_pool/   mining_pool.csv + images/    (input_path relative to this dir)

Config mapping (ExperimentConfig -> workspace):
    data_pool.path              -> workspace root (local path or file:// URI)
    model.checkpoint            -> base checkpoint (absolute or workspace-relative)
    training.initial_spec_path  -> train spec      (default specs/baseline_spec.yaml)
    kpi.inference_spec_path     -> inference spec  (default specs/baseline_inference_spec.yaml)
    kpi.path                    -> KPI csv         (default kpi/testing_set.csv)

TAO builds an image path as {images_dir}/{input_path}/{object_name}_{light}.jpg,
so `input_path`/`golden_path` are directories. Base CSVs are relative to
kpi/images; per-iteration combined CSVs are re-rooted to the workspace root
(images_dir=/data/workspace) by prefixing `kpi/images/`. Mined pool rows use
input_path=augmentation/mining_pool/images and golden_path=kpi/images/<pool
golden_path> — the pool's goldens live in the shared kpi/images tree.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from app.config import CONTAINER_WORKSPACE

REQUIRED_COLUMNS = ("input_path", "golden_path", "label", "object_name")
LEAKAGE_KEY = ("input_path", "golden_path", "label", "object_name", "boardname")
DEFAULT_LIGHT = "SolderLight"
PASS_LABEL = "PASS"


class WorkspaceError(ValueError):
    pass


def _strip_file_uri(path: str) -> str:
    return path[len("file://"):] if path.startswith("file://") else path


@dataclass
class Workspace:
    root: Path

    @staticmethod
    def resolve(
        data_pool_path: str,
        *,
        allowed_root: str | Path | None = None,
    ) -> "Workspace":
        """Resolve a local workspace within an optional deployment boundary.

        ``allowed_root`` protects Docker-socket deployments from path
        confusion: DEFT passes the resolved workspace to the host daemon as a
        bind source, so the path must identify the same deployment-owned
        directory in both filesystem namespaces.
        """
        raw = _strip_file_uri(data_pool_path)
        if raw.startswith(("s3://", "lustre://", "http://", "https://")):
            raise WorkspaceError(
                f"the docker backend requires a local workspace path; got {data_pool_path!r}. "
                "Stage the workspace on the service host and pass its filesystem path "
                "(or file:// URI) as data_pool.path."
            )
        root = Path(raw).expanduser().resolve()
        if allowed_root is not None:
            boundary = Path(allowed_root).expanduser().resolve()
            try:
                root.relative_to(boundary)
            except ValueError as exc:
                raise WorkspaceError(
                    f"workspace root {root} is outside the allowed workspace "
                    f"root {boundary}"
                ) from exc
        if not root.is_dir():
            raise WorkspaceError(f"workspace root {root} does not exist or is not a directory")
        return Workspace(root=root)

    # -- path helpers -------------------------------------------------------
    def host(self, rel_or_abs: str) -> Path:
        p = Path(_strip_file_uri(rel_or_abs))
        return p if p.is_absolute() else self.root / p

    def container(self, host_path: Path | str) -> str:
        """Map a host path under the workspace to its in-container path."""
        p = Path(host_path).resolve()
        try:
            rel = p.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError(
                f"{p} is outside the workspace {self.root}; containers only mount the workspace"
            ) from exc
        return f"{CONTAINER_WORKSPACE}/{rel}"

    def from_container(self, container_path: str) -> Path | None:
        """Inverse of :meth:`container`: map an in-container path to the host.

        Stage arguments carry container paths (``results_dir=/data/workspace/
        ...``) because that is what the container sees; this service reads the
        same files through ``self.root``. Returns None when the path is not
        under the container mount, so callers can treat "not a workspace path"
        as absent rather than as an error.
        """
        p = Path(_strip_file_uri(container_path))
        try:
            rel = p.relative_to(CONTAINER_WORKSPACE)
        except ValueError:
            return None
        return self.root / rel

    # -- workspace files ------------------------------------------------------
    @property
    def train_csv(self) -> Path:
        return self.root / "train/base/training_set.csv"

    @property
    def val_csv(self) -> Path:
        return self.root / "train/base/validation_set.csv"

    @property
    def pool_dir(self) -> Path:
        return self.root / "augmentation/mining_pool"

    @property
    def pool_csv(self) -> Path:
        return self.pool_dir / "mining_pool.csv"


# ---------------------------------------------------------------------------
# CSV utilities (stdlib csv — the service stays numpy/pandas-free)
# ---------------------------------------------------------------------------

def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise WorkspaceError(f"{path} is empty or has no header")
        rows = [dict(r) for r in reader]
    return list(reader.fieldnames), rows


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def check_columns(path: Path, fieldnames: list[str]) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
    if missing:
        raise WorkspaceError(f"{path} is missing required ChangeNet columns: {missing}")


def normalize_label(label: str) -> str:
    """The ChangeNet dataloader treats PASS (exact, uppercase) as the pass
    class sentinel; every other label is normalized lowercase."""
    label = (label or "").strip()
    return label if label == PASS_LABEL else label.lower()


def leakage_pairs(rows_a: list[dict], rows_b: list[dict]) -> int:
    def key(row: dict) -> tuple:
        return tuple((row.get(k) or "").strip() for k in LEAKAGE_KEY)

    keys_b = {key(r) for r in rows_b}
    return sum(1 for r in rows_a if key(r) in keys_b)


def validate_existence(ws: Workspace, rows: list[dict], images_dir_rel: str) -> list[str]:
    """Check every input_path/golden_path resolves on disk (as the directory
    TAO will join object/light names into). Returns missing-path strings."""
    base = ws.host(images_dir_rel) if images_dir_rel else ws.root
    missing: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for col in ("input_path", "golden_path"):
            rel = (row.get(col) or "").strip()
            if not rel or rel in seen:
                continue
            seen.add(rel)
            p = base / rel
            if not (p.is_dir() or p.is_file()):
                missing.append(f"{col}: {rel}")
    return missing


def image_file(base_dir: Path, input_path: str, object_name: str, light: str = DEFAULT_LIGHT) -> Path:
    """The concrete image file TAO would load for a CSV row."""
    p = base_dir / input_path
    if p.is_file():
        return p
    return p / f"{object_name}_{light}.jpg"
