"""Object-store layout + keys for the Eval-set pipeline (design §8).

Layout (self-describing, derivable from eval_id alone):

    <bucket>/<prefix>/<eval_id>/
        status.json          # progress + terminal state (running|ready|failed)
        manifest0.jsonl       # one line per record in slice 0
        ...
        manifest<N-1>.jsonl
        eval.json         # aggregate summary, written on finalise
"""

from eval_set_worker.config import settings


def eval_dir(eval_id: str) -> str:
    return f"{settings.eval_prefix}/{eval_id}"


def status_key(eval_id: str) -> str:
    return f"{eval_dir(eval_id)}/status.json"


def summary_key(eval_id: str) -> str:
    return f"{eval_dir(eval_id)}/eval.json"


def manifest_key(eval_id: str, slice_id: int) -> str:
    return f"{eval_dir(eval_id)}/manifest{slice_id}.jsonl"


def manifest_uri(eval_id: str, slice_id: int) -> str:
    """s3:// URI for a manifest, as returned in the API's manifests[].uri."""
    return f"s3://{settings.aws_bucket}/{manifest_key(eval_id, slice_id)}"


def status_prefix(eval_id: str) -> str:
    """Prefix under which per-slice durable completion markers live."""
    return f"{eval_dir(eval_id)}/_status/"


def slice_marker_key(eval_id: str, slice_id: int) -> str:
    """Durable per-slice completion marker — the restart-safe source of truth the
    summary consumer aggregates from (Kafka completions are only wake-ups)."""
    return f"{status_prefix(eval_id)}slice{slice_id}.json"
