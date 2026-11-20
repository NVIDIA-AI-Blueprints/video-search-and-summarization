"""
Seed Alert Bridge's prompt registry from the GT file.

Called once at evaluator startup (after AB reports healthy) so the evaluator
can submit ``/api/v1/verification/ondemand`` requests using GT-derived
``category`` values without relying on an out-of-band edit to AB's
``alert_type_config.json``.

The seeding flow is idempotent:

1. Read the GT file (BCQ ``bcq``/``items`` or legacy ``dataframe``).
2. For each ``(idx, entry)``, derive an AB-compatible slug via
   :func:`services.category_resolver.gt_row_slug`. The row index is what
   keeps duplicate-video rows distinct so each row's prompt lands under its
   own slug (the previous resolver collapsed duplicate-video rows together
   and dropped all but the last prompt).
3. ``upsert_alert_config(slug, prompt=question, system_prompt=...)``.
   The client falls back to PUT when AB returns 409 ("already exists") and
   returns ``(body, was_updated)`` so we can count create-vs-update
   without poking at log state.

The helper returns a :class:`SeedSummary` so the caller can log how many
slugs were created vs updated vs failed. It never raises on per-entry
failures — those are aggregated and reported to keep startup resilient when
a handful of entries are malformed.
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..services.alert_bridge_client import AlertBridgeClient, AlertBridgeError
from ..services.category_resolver import gt_row_slug

logger = logging.getLogger(__name__)


@dataclass
class SeedSummary:
    """Per-run accounting of the seeding pass."""

    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    failures: List[str] = field(default_factory=list)
    slugs: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
            "slug_count": len(self.slugs),
            "first_failures": self.failures[:5],
        }


def _load_gt_entries(gt_file_path: str) -> List[Dict[str, Any]]:
    """Best-effort load of GT entries supporting all formats the evaluator handles.

    Mirrors the logic in ``EvaluationService.load_ground_truth`` so the seed
    pass and the per-incident eval pass agree on which entries exist.
    """
    path = Path(gt_file_path)
    if not path.exists():
        logger.warning("Seed: GT file not found at %s — skipping seed pass", gt_file_path)
        return []
    try:
        with path.open("r") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        logger.error("Seed: GT file is not valid JSON (%s): %s", gt_file_path, exc)
        return []

    if isinstance(data, dict):
        if "bcq" in data and isinstance(data["bcq"], list):
            return data["bcq"]
        if "items" in data and isinstance(data["items"], list):
            return data["items"]
        if "dataframe" in data and isinstance(data["dataframe"], list):
            return data["dataframe"]
        # Events GT (eval v2 latency / count format) doesn't carry prompts —
        # nothing to seed.
        if "events" in data or "sensors" in data:
            logger.info(
                "Seed: GT is in events format — no per-entry prompts to seed",
            )
            return []
    if isinstance(data, list):
        return data
    logger.warning(
        "Seed: GT file %s has an unrecognised top-level structure — skipping",
        gt_file_path,
    )
    return []


def _entry_to_seed_payload(
    entry: Dict[str, Any],
    row_index: int,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Pull (alert_type, payload) out of a single GT entry.

    Returns None for entries that don't carry enough information to seed
    (no prompt/question). Such entries are counted as "skipped" rather than
    "failed". The slug is always derivable thanks to ``gt_row_slug``'s
    ``row{idx}`` last-resort, so we never skip on slug alone.
    """
    if not isinstance(entry, dict):
        return None
    # The "user prompt" upstream is whatever question we want VLM to answer.
    # GT files use either `question` (BCQ) or `prompt` (dataframe).
    user_prompt = entry.get("question") or entry.get("prompt")
    if not isinstance(user_prompt, str) or not user_prompt.strip():
        return None

    slug = gt_row_slug(entry, row_index)
    payload: Dict[str, Any] = {"prompt": user_prompt.strip()}
    system_prompt = entry.get("system_prompt")
    if isinstance(system_prompt, str) and system_prompt.strip():
        payload["system_prompt"] = system_prompt.strip()
    return slug, payload


def _seed_one(
    client: AlertBridgeClient,
    slug: str,
    payload: Dict[str, Any],
) -> Tuple[str, str]:
    """Seed a single (slug, payload) pair via upsert.

    Returns ``(slug, outcome)`` where ``outcome`` is one of
    ``"created"`` / ``"updated"`` / ``"failed:<msg>"`` so the caller can
    aggregate without coupling to the client return shape.
    """
    try:
        _, was_updated = client.upsert_alert_config(alert_type=slug, **payload)
        return slug, "updated" if was_updated else "created"
    except AlertBridgeError as exc:
        return slug, f"failed:{exc}"
    except Exception as exc:  # pragma: no cover — last-resort guard
        return slug, f"failed:{exc!r}"


_HEALTHY_STATUSES = frozenset({"healthy", "ok"})


def wait_for_alert_bridge(
    client: AlertBridgeClient,
    timeout_seconds: float = 60.0,
    poll_interval_seconds: float = 2.0,
) -> bool:
    """Block until AB responds healthy or ``timeout_seconds`` elapses.

    Uses the existing :meth:`AlertBridgeClient.health_check` so it picks up
    the same ``base_url`` / timeouts as the rest of the client.

    Accepts both ``status == "healthy"`` and ``status == "ok"``. Upstream AB
    (the production ``vss-alert-verification`` image) returns
    ``{"status": "ok", "message": "Alert Bridge is running"}`` while older
    forks returned ``"healthy"``; the evaluator can't pick one and break
    parity with the other deployment, so we accept either.

    Returns ``True`` when AB reports healthy, ``False`` otherwise. Never raises.
    """
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    attempts = 0
    while True:
        attempts += 1
        result = client.health_check()
        if str(result.get("status", "")).lower() in _HEALTHY_STATUSES:
            logger.info(
                "Alert Bridge is healthy after %s attempt(s) (status=%s)",
                attempts, result.get("status"),
            )
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "Alert Bridge did not become healthy within %.1fs (last=%s)",
                timeout_seconds, result,
            )
            return False
        time.sleep(min(poll_interval_seconds, remaining))


def seed_alert_types_from_gt(
    client: AlertBridgeClient,
    gt_file_path: str,
    max_workers: int = 8,
) -> SeedSummary:
    """Seed every GT entry's ``(slug -> prompt)`` pair into AB.

    Each GT row gets its own slug (via :func:`gt_row_slug`) so duplicate-
    video rows do not collide. Slugs that genuinely repeat (e.g. two rows
    sharing the same explicit ``id``) still collapse via ``deduped`` —
    last write wins, matching AB's PUT-after-POST upsert semantics.

    Args:
        client: An initialised :class:`AlertBridgeClient`.
        gt_file_path: Path to the GT JSON. Read by :func:`_load_gt_entries`.
        max_workers: Thread-pool size for parallel POSTs. Tuned conservatively
            (8) so we don't drown a small AB Redis instance during startup.

    Returns:
        :class:`SeedSummary` with counts and a sample of failure messages
        (first 5) for log-friendliness.
    """
    summary = SeedSummary()
    entries = _load_gt_entries(gt_file_path)
    summary.total = len(entries)
    if not entries:
        return summary

    work: List[Tuple[str, Dict[str, Any]]] = []
    for idx, entry in enumerate(entries):
        pair = _entry_to_seed_payload(entry, idx)
        if pair is None:
            summary.skipped += 1
            continue
        work.append(pair)
    if not work:
        logger.info("Seed: no GT entries carried a (slug, prompt) pair")
        return summary

    deduped: Dict[str, Dict[str, Any]] = {}
    for slug, payload in work:
        # Last write wins, matching AB's PUT-after-POST upsert semantics.
        # With per-row slugs this only fires when two rows share an explicit
        # ``id``; same-video-different-question rows now stay distinct.
        deduped[slug] = payload
    logger.info(
        "Seed: posting %d unique alert_type configs to %s",
        len(deduped), client.ondemand_url.rsplit('/api/', 1)[0],
    )

    with ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="seed-ab") as ex:
        futures = [
            ex.submit(_seed_one, client, slug, payload)
            for slug, payload in deduped.items()
        ]
        for fut in futures:
            slug, outcome = fut.result()
            summary.slugs.append(slug)
            if outcome == "created":
                summary.created += 1
            elif outcome == "updated":
                summary.updated += 1
            else:
                summary.failed += 1
                if outcome.startswith("failed:"):
                    summary.failures.append(f"{slug}: {outcome[7:]}")
                else:
                    summary.failures.append(f"{slug}: {outcome}")

    logger.info(
        "Seed: done — created=%d updated=%d skipped=%d failed=%d (of %d entries)",
        summary.created, summary.updated, summary.skipped, summary.failed, summary.total,
    )
    if summary.failures:
        for line in summary.failures[:5]:
            logger.warning("Seed failure: %s", line)
    return summary


def cleanup_alert_types(
    client: AlertBridgeClient,
    slugs: Iterable[str],
    max_workers: int = 8,
) -> Tuple[int, int]:
    """Best-effort delete pass for the slugs we created earlier.

    Used at shutdown / batch-teardown when the caller wants AB's Redis to be
    free of evaluator-seeded entries. Failures are logged but never raised
    so cleanup never blocks shutdown.

    Returns ``(deleted_count, missing_count)`` for caller-side reporting.
    """
    deleted = 0
    missing = 0
    slugs = list(slugs)
    if not slugs:
        return 0, 0
    with ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="cleanup-ab") as ex:
        futures = {ex.submit(client.delete_alert_config, s): s for s in slugs}
        for fut, slug in futures.items():
            try:
                ok = fut.result()
            except Exception as exc:
                logger.warning("Cleanup: failed to delete %s: %s", slug, exc)
                continue
            if ok:
                deleted += 1
            else:
                missing += 1
    logger.info("Cleanup: deleted=%d already-missing=%d", deleted, missing)
    return deleted, missing
