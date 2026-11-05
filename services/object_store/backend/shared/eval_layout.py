"""Object-store key helpers for the Eval-set API (design §8).

Layout (self-describing, derivable from eval_id alone):

    <bucket>/<prefix>/<eval_id>/
        status.json          # progress + terminal state (running|ready|failed)
        manifest0.jsonl ...  # one per slice, written by eval_set_worker
        eval.json         # aggregate summary, written by eval_set_worker

The API only needs the status/summary keys; the worker owns manifest keys + line
construction + the s3:// manifest URIs returned by the API (see eval_set_worker/core).
"""

from backend.config import settings


def eval_dir(eval_id: str) -> str:
    return f"{settings.eval_prefix}/{eval_id}"


def status_key(eval_id: str) -> str:
    return f"{eval_dir(eval_id)}/status.json"


def summary_key(eval_id: str) -> str:
    return f"{eval_dir(eval_id)}/eval.json"
