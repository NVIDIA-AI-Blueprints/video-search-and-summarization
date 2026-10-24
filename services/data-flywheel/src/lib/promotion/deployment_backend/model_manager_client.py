# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""HTTP deployment backend that talks to the standalone Model Manager service.

Implements the Model Promotion API verbs (pull / apply / convergence / cleanup,
plus rollback expressed as ``apply(full_switch, previous)``) as real ``requests``
calls against ``model_manager.base_url``. Responses are parsed into the
``base.py`` dataclasses; non-2xx ``ErrorEnvelope`` bodies become typed
:class:`BackendError`; transient failures (connection/timeout/HTTP 5xx) are
retried with exponential backoff.

The retry helper is local rather than ``src/lib/retry.py`` because it also needs
``BackendError.retryable``. Its POSTs are retry-safe: pull/apply carry
idempotency keys, so a re-send after an ambiguous timeout cannot double-apply.
"""

from time import sleep
from typing import Any

import requests

from src.lib.promotion.deployment_backend.base import (
    ApplyResult,
    BackendError,
    ConvergenceReason,
    ConvergenceReport,
    DeploymentBackend,
    PromotionControl,
    PullResult,
    RoutingEntry,
    ServingState,
)
from src.lib.promotion.deployment_backend.factory import DeploymentBackendFactory
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.promotion.deployment_backend.model_manager")

# Retry configuration for transient errors (mirrors src/lib/retry.py).
API_MAX_RETRIES = 3
API_RETRY_BACKOFF_FACTOR = 2.0  # seconds: 2, 4, 8

# Fallback ErrorEnvelope codes keyed by HTTP status when the body has no envelope.
_STATUS_FALLBACK_CODE = {
    404: "not_found",
    410: "gone",
    409: "conflict",
    422: "bad_request",
}


def _is_transient_error(error: Exception) -> bool:
    """Return True if ``error`` is a transient failure worth retrying.

    Transient = connection/timeout errors, or a :class:`BackendError` the server
    marked ``retryable`` (which we also set for un-enveloped HTTP 5xx responses).
    """
    if isinstance(error, BackendError):
        return error.retryable
    return (
        isinstance(error, requests.exceptions.ConnectionError | requests.exceptions.Timeout)
        or isinstance(error, TimeoutError)
        or "RemoteDisconnected" in str(error)
        or "Connection aborted" in str(error)
        or "Connection reset" in str(error)
        or "timed out" in str(error).lower()
    )


def _list_field(data: dict, key: str) -> list | None:
    """Read an array field; ``None`` when it is present but not an array.

    An *absent* key is legitimate — an older or partial payload simply omits it —
    and yields an empty list. A key that is present with ``null`` or a non-array
    value is a malformed payload: the sender claimed to answer and the answer is
    unusable. Those are distinguished by key presence, because ``dict.get`` maps
    both to ``None``.

    ``str``/``bytes`` are rejected despite being iterable: a bare string would
    otherwise be read element-wise into a plausible-looking list of characters.
    """
    if key not in data:
        return []
    value = data[key]
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return None


@DeploymentBackendFactory.register("model_manager")
class ModelManagerBackend(DeploymentBackend):
    """Deployment backend implemented as an HTTP client to the Model Manager.

    Verb -> endpoint mapping (Model Promotion API):
        pull        -> POST /v1/models/pull
        apply       -> POST /v1/models/apply
        convergence -> POST /v1/models/convergence
        unload      -> POST /v1/models/unload
        cleanup     -> POST /v1/models/cleanup
        rollback    -> apply(full_switch, previous_version)

    There is deliberately no ``status`` row. ``GET /v1/models/status/{swap_id}``
    is a 410 tombstone: a swap id was process-local to whichever Model Manager
    minted it, so it could not answer across a restart or a second replica.
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize with ``base_url`` and ``timeout`` from system config.

        ``pull_timeout`` is a dedicated, longer read budget for the pull POST
        only: a real >600MB weight download inside the Model Manager regularly
        exceeds the short ``timeout`` (default 30s), so apply/convergence/cleanup
        keep ``timeout`` while pull gets ``pull_timeout``. ``convergence`` is the
        hot path -- it runs on every poll -- and the short budget is what keeps an
        unreachable control plane costing one tick rather than one stall.
        Backward compatible — the
        default (900s) applies when unset.
        """
        self.base_url: str = (config.get("base_url") or "http://localhost:8090").rstrip("/")
        self.timeout: float = float(config.get("timeout", 30))
        self.pull_timeout: float = float(config.get("pull_timeout", 900))
        self.max_retries: int = int(config.get("max_retries", API_MAX_RETRIES))
        self.backoff_factor: float = float(
            config.get("retry_backoff_factor", API_RETRY_BACKOFF_FACTOR)
        )

    # ------------------------------------------------------------------ #
    # HTTP plumbing
    # ------------------------------------------------------------------ #
    def _retry(self, operation_name: str, func):
        """Execute ``func`` with bounded exponential-backoff retries on transients."""
        for attempt in range(1, self.max_retries + 1):
            try:
                return func()
            except Exception as e:
                if _is_transient_error(e) and attempt < self.max_retries:
                    wait_time = self.backoff_factor**attempt
                    logger.warning(
                        "Transient error in %s (attempt %d/%d): %s. Retrying in %.0fs...",
                        operation_name,
                        attempt,
                        self.max_retries,
                        e,
                        wait_time,
                    )
                    sleep(wait_time)
                    continue
                raise

    def _handle_response(self, resp: requests.Response) -> dict:
        """Return parsed JSON for 2xx, else raise a typed :class:`BackendError`."""
        if 200 <= resp.status_code < 300:
            return resp.json() if resp.content else {}
        raise self._error_from_response(resp)

    def _error_from_response(self, resp: requests.Response) -> BackendError:
        """Translate a non-2xx response (ErrorEnvelope when present) to BackendError."""
        # Un-enveloped 5xx is treated as retryable; other statuses default to not.
        code = _STATUS_FALLBACK_CODE.get(resp.status_code, "internal")
        message = f"HTTP {resp.status_code} from {resp.url}"
        retryable = resp.status_code >= 500
        details = None
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                code = err.get("code", code)
                message = err.get("message", message)
                retryable = bool(err.get("retryable", retryable))
                details = err.get("details")
        return BackendError(
            code=code,
            message=message,
            retryable=retryable,
            http_status=resp.status_code,
            details=details,
        )

    def _post(self, path: str, json_body: dict, *, timeout: float | None = None) -> dict:
        # An explicit None-check, so a caller's falsy override (e.g. 0) is
        # honored rather than falling back to the default.
        resp = requests.post(
            f"{self.base_url}{path}",
            json=json_body,
            timeout=self.timeout if timeout is None else timeout,
        )
        return self._handle_response(resp)

    def _get(self, path: str) -> dict:
        resp = requests.get(f"{self.base_url}{path}", timeout=self.timeout)
        return self._handle_response(resp)

    # ------------------------------------------------------------------ #
    # Model Promotion API verbs
    # ------------------------------------------------------------------ #
    def pull(
        self,
        model_name: str,
        version: str,
        artifact_uri: str,
        fmt: str,
        sha256: str,
        *,
        spec_uri: str | None = None,
        threshold: float | None = None,
        idempotency_key: str | None = None,
    ) -> PullResult:
        # Never log the uris: a presigned one carries credentials in its query.
        logger.info("pull %s@%s (%s)", model_name, version, fmt)
        body: dict[str, Any] = {
            "model_name": model_name,
            "version": version,
            "artifact_uri": artifact_uri,
            "format": fmt,
            "sha256": sha256,
        }
        if spec_uri is not None:
            body["spec_uri"] = spec_uri
        if threshold is not None:
            body["threshold"] = threshold
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key
        # The weight download runs inside this POST, hence ``pull_timeout``.
        data = self._retry(
            "pull", lambda: self._post("/v1/models/pull", body, timeout=self.pull_timeout)
        )
        return PullResult(
            model_name=data["model_name"],
            version=data["version"],
            repo_path=data["repo_path"],
            status=data["status"],
        )

    def apply(
        self,
        model_name: str,
        version: str,
        control: PromotionControl,
        *,
        canary_weight: float | None = None,
        idempotency_key: str | None = None,
    ) -> ApplyResult:
        logger.info("apply %s@%s control=%s", model_name, version, control.value)
        body: dict[str, Any] = {
            "model_name": model_name,
            "version": version,
            "control": control.value,
        }
        if canary_weight is not None:
            body["canary_weight"] = canary_weight
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key
        data = self._retry("apply", lambda: self._post("/v1/models/apply", body))
        applied = data.get("applied")
        if not isinstance(applied, dict) or not applied:
            # Not a KeyError: advance() must fail the gate, not retry in Celery.
            raise BackendError(
                "bad_request",
                "apply response carried no 'applied' object; deploy a Model Manager "
                "that mints the stateless convergence poll token first",
                retryable=False,
            )
        return ApplyResult(
            applied=applied,
            to_version=data["to_version"],
            control=PromotionControl(data["control"]),
            started_at=data["started_at"],
            from_version=data.get("from_version"),
            noop=bool(data.get("noop", False)),
            swap_id=data.get("swap_id"),
        )

    def convergence(self, applied: dict[str, Any]) -> ConvergenceReport:
        """Ask whether ``applied`` has landed. The token is echoed back as-is.

        Pass the dict straight through: both sides forbid extra fields, so a
        re-serialised DFW-side mirror would 422 the moment either side gains one.

        Unlike ``serving_state``, errors are not swallowed: the poll loop must
        tell "not converged yet" apart from "could not ask".
        """
        try:
            data = self._retry(
                "convergence",
                lambda: self._post("/v1/models/convergence", {"expectation": dict(applied)}),
            )
        except requests.exceptions.RequestException as exc:
            # Every transport failure becomes one shape, so a network blip does
            # not fail a promotion. ``http_status`` stays None, which is not in
            # the (400, 422) set that ends a gate.
            raise BackendError(
                "internal",
                f"convergence poll could not reach the Model Manager: {exc}",
                retryable=True,
            ) from exc
        raw = data.get("reason")
        reason = None
        if isinstance(raw, dict):
            reason = ConvergenceReason(
                code=str(raw.get("code", "unknown")),
                message=str(raw.get("message", "")),
                terminal=bool(raw.get("terminal", False)),
                detail=raw.get("detail") if isinstance(raw.get("detail"), dict) else None,
            )
        # An unreadable body reads as "not yet", never as terminal.
        return ConvergenceReport(converged=bool(data.get("converged", False)), reason=reason)

    def rollback(
        self,
        model_name: str,
        previous_version: str,
        *,
        idempotency_key: str | None = None,
    ) -> ApplyResult:
        # Rollback has no endpoint: it is apply(full_switch, previous_version).
        return self.apply(
            model_name,
            previous_version,
            PromotionControl.FULL_SWITCH,
            idempotency_key=idempotency_key,
        )

    def serving_state(self, model_name: str) -> ServingState | None:
        """Read what the serving stack currently holds for ``model_name``.

        Answers ``None`` -- "unknown" -- rather than raising, for any reason:
        a 404 from an older Model Manager, an unreachable service, a malformed
        body. The caller picks a version floor from this and has others to fall
        back on, so a failed read must degrade rather than fail a promotion.

        Not retried: it is a hint on the fast path to minting.
        """
        try:
            data = self._get(f"/v1/models/{model_name}/serving-state")
        except BackendError as exc:
            # 404 means "predates the endpoint", so it is logged quietly.
            level = logger.debug if exc.http_status == 404 else logger.warning
            level(
                "serving-state unavailable for %s (%s); minting without it",
                model_name,
                exc.code,
            )
            return None
        except Exception:
            logger.warning(
                "serving-state read failed for %s; minting without it",
                model_name,
                exc_info=True,
            )
            return None

        if not isinstance(data, dict):
            logger.warning("serving-state for %s was not an object; ignoring", model_name)
            return None

        # Not ``dict.get(k, [])``: it does not fall back on a present-but-null
        # key, and a JSON ``null`` then raised on iteration. A bare string is the
        # other trap -- ``known_versions: "12"`` yields an empty list, and empty
        # reads as "no versions are taken", which mints onto a live one.
        raw_routing = _list_field(data, "routing")
        raw_serving = _list_field(data, "serving_versions")
        raw_known = _list_field(data, "known_versions")
        if raw_routing is None or raw_serving is None or raw_known is None:
            # Present but not an array. Keeping the parts that DID parse would
            # read as "the stack holds nothing" and mint from the day-0 floor
            # onto a live version: an untrustworthy answer must be no answer.
            logger.warning(
                "serving-state for %s had a malformed list field; treating it as unknown",
                model_name,
            )
            return None

        routing = [
            RoutingEntry(version=r["version"], role=r["role"], weight=r["weight"])
            for r in raw_routing
            if isinstance(r, dict) and {"version", "role", "weight"} <= r.keys()
        ]
        # Absent or malformed is None, never []: an older Model Manager omits the
        # field, and [] would read as "nothing is servable" and refuse every
        # rollback. Only the rollback precheck reads it, so it is not fatal here.
        raw_ready = data.get("ready_versions")
        ready_versions = [str(v) for v in raw_ready] if isinstance(raw_ready, list) else None
        return ServingState(
            model_name=data.get("model_name", model_name),
            default_version=data.get("default_version"),
            routing=routing,
            serving_versions=[str(v) for v in raw_serving],
            known_versions=[str(v) for v in raw_known],
            ready_versions=ready_versions,
            ready=bool(data.get("ready", False)),
            # Absent flags come from a payload that does not model the
            # distinction, so they read as UNKNOWN.
            routing_available=bool(data.get("routing_available", False)),
            known_available=bool(data.get("loaded_available", data.get("known_available", False))),
        )

    def unload(self, model_name: str, version: str) -> dict:
        """Evict a version from serving memory. Reclaims nothing, reports nothing.

        Not built on :meth:`cleanup`: that endpoint deletes the directory, which
        at a full switch is the rollback target. The missing ``disk_reclaimed``
        is the contract here, not a degraded outcome, so ``cleanup``'s body
        parsing is not reused either -- its warning would fire every promotion.
        """
        logger.info("unload %s@%s", model_name, version)
        body = {"model_name": model_name, "version": version}
        try:
            data = self._retry("unload", lambda: self._post("/v1/models/unload", body))
        except BackendError as exc:
            if exc.http_status == 404:
                # No fallback to /v1/models/cleanup: held GPU is recoverable, a
                # deleted baseline is not.
                logger.error(
                    "unload of %s@%s: this Model Manager has no POST /v1/models/unload. "
                    "Retired versions will keep holding GPU until it is upgraded. NOT "
                    "falling back to /v1/models/cleanup, which would delete the rollback "
                    "target.",
                    model_name,
                    version,
                )
            raise
        # Indexed, not ``.get``: a missing key is a contract we do not
        # understand, and reading that as success is worse.
        return {"version": data["version"], "status": data["status"]}

    def cleanup(
        self,
        model_name: str,
        version: str,
        *,
        min_age_s: float | None = None,
        dry_run: bool = False,
        evicted_by_caller: bool = False,
    ) -> dict:
        """Reclaim a version's directory, and report whether that happened.

        ``disk_reclaimed`` is passed through, not dropped: cleanup returns 200
        because the eviction succeeded, so a version left on disk is a degraded
        outcome rather than a failure, and this field is the only signal that the
        shared repository is growing.
        """
        logger.info(
            "cleanup %s@%s (min_age_s=%s dry_run=%s evicted_by_caller=%s)",
            model_name,
            version,
            min_age_s,
            dry_run,
            evicted_by_caller,
        )
        body: dict[str, Any] = {"model_name": model_name, "version": version}
        # Omitted at their defaults, so an older Model Manager sees a
        # byte-identical request.
        if min_age_s is not None:
            body["min_age_s"] = min_age_s
        if dry_run:
            body["dry_run"] = dry_run
        if evicted_by_caller:
            body["evicted_by_caller"] = evicted_by_caller
        data = self._retry("cleanup", lambda: self._post("/v1/models/cleanup", body))
        # An absent field is "unknown", not "reclaimed": manufacturing the
        # reassuring answer is how a growing volume stays invisible.
        reported = data.get("disk_reclaimed")
        disk_reclaimed = bool(reported) if reported is not None else False
        if reported is None:
            logger.warning(
                "cleanup of %s@%s returned no disk_reclaimed field, so whether its directory "
                "was reclaimed is unknown; upgrade the Model Manager to find out",
                model_name,
                version,
            )
        elif not disk_reclaimed and dry_run:
            # The requested outcome, and ``dry_run`` ships True: a warning here
            # would fire per nominee per tick forever and get filtered.
            logger.info(
                "cleanup of %s@%s withheld the reclaim as asked (dry run); its "
                "directory is still on the shared model repository",
                model_name,
                version,
            )
        elif not disk_reclaimed:
            logger.warning(
                "cleanup of %s@%s returned 200 but did not reclaim its directory; "
                "the shared model repository is still holding it",
                model_name,
                version,
            )
        return {
            "version": data["version"],
            "status": data["status"],
            "disk_reclaimed": disk_reclaimed,
        }
