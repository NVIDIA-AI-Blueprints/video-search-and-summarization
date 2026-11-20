"""
Alert Bridge API Client

Handles communication with Alert Bridge for on-demand VLM verification and
runtime prompt seeding.

Available endpoints:

* ``POST /api/v1/verification/ondemand`` — async verification request, returns
  202 with a correlationId. Results are published asynchronously to Kafka /
  Elasticsearch by AB's pipeline; the evaluator polls ES for them.
* ``POST /api/v1/incidents`` — NvSchema incident ingress. Alert Agent publishes
  accepted incidents to Kafka, exercising the normal message-bus worker path.
* ``POST /api/v1/verification/config`` — create an alert_type config (prompt,
  system_prompt, vlm_params).
* ``GET /api/v1/verification/config/{alert_type}`` — preflight: check if a
  category is seeded.
* ``PUT /api/v1/verification/config/{alert_type}`` — partial update with deep
  merge for ``vlm_params``.
* ``DELETE /api/v1/verification/config/{alert_type}`` — teardown.
* ``GET /health`` — service health check.

The seeding flow (``create/update/delete_alert_config``) lets the evaluator
own the prompt-config lifecycle for the duration of an eval batch, without
requiring an out-of-band edit to ``alert_type_config.json`` in the Alert
Bridge deployment repo. AB's static seed file is still authoritative on
startup (``prompt.override_prompts_on_start: true``); the runtime calls add
the GT-derived alert types on top of that baseline.
"""

import logging
import threading
from typing import Any, Dict, Optional, Tuple

import requests

from ..config import Settings

logger = logging.getLogger(__name__)


class AlertBridgeError(Exception):
    """Base class for Alert Bridge API errors."""


class UnknownCategoryError(AlertBridgeError):
    """Raised when AB rejects a request because the `category` has no prompt
    configured (HTTP 400 with `error == "unknown_category"`).

    Surfacing this distinct from generic AB errors lets the evaluator point
    operators at the right fix: seed `alert_type_config.json` and restart AB,
    or POST to `/api/v1/verification/config`.
    """


class AlertBridgeClient:
    """Thin HTTP client for Alert Bridge's verification API.

    Sessions are thread-local: ``requests.Session`` is documented as not
    guaranteed thread-safe (mutable cookie jar / adapter state), and the
    seed pass posts in parallel via :class:`concurrent.futures.ThreadPoolExecutor`.
    Handing each worker its own session keeps connection pooling within a
    thread and avoids cross-thread state races without a global lock.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._base_url = settings.alert_bridge_url.rstrip('/')
        self._endpoint_path = '/' + settings.alert_bridge_endpoint_path.lstrip('/')
        self._incident_endpoint_path = '/' + settings.alert_bridge_incident_endpoint_path.lstrip('/')
        self._timeout = settings.alert_bridge_timeout
        # Per-thread session (see class docstring). Tests can still inject a
        # ``MagicMock`` via ``client._session = mock`` thanks to the setter
        # below, which stores the value on this same TLS slot.
        self._tls = threading.local()

    @property
    def _session(self) -> requests.Session:
        s = getattr(self._tls, "session", None)
        if s is None:
            s = requests.Session()
            self._tls.session = s
        return s

    @_session.setter
    def _session(self, value) -> None:
        """Setter exists primarily so unit tests can swap in a ``MagicMock``."""
        self._tls.session = value

    @property
    def ondemand_url(self) -> str:
        return f"{self._base_url}{self._endpoint_path}"

    @property
    def incident_url(self) -> str:
        return f"{self._base_url}{self._incident_endpoint_path}"

    def health_check(self) -> Dict[str, Any]:
        """Check Alert Bridge service health."""
        try:
            response = self._session.get(
                f"{self._base_url}/health",
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Alert Bridge health check failed: {e}")
            return {"status": "unhealthy", "error": str(e)}

    def submit_ondemand_verification(
        self, message: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Submit a single message to ``/api/v1/verification/ondemand``.

        The schema (see ``alert-agent-web/app/schema/verification_schemas.py``)
        requires ``category`` plus ``info.media_urls`` (non-empty list) and
        ``info.media_type`` (``"video"`` | ``"image"``). Any extra Incident
        fields (``id``, ``sensorId``, ``timestamp``, ``end``, ``place``, …)
        are accepted via ``extra="allow"``.

        Returns the parsed 202 response body, e.g.::

            {
                "status": "accepted",
                "correlationId": "<id>",
                "message": "Verification request accepted for processing",
                "timestamp": "2026-04-22T17:38:38Z"
            }

        Raises:
            UnknownCategoryError: AB returned 400 ``unknown_category`` —
                the requested ``category`` is not registered in
                ``alert_type_config.json`` (or in Redis if runtime PUTs are in use).
            AlertBridgeError: any other transport / 4xx / 5xx failure.
        """
        try:
            response = self._session.post(
                self.ondemand_url,
                json=message,
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            logger.error(
                "POST %s failed at the transport layer: %s",
                self.ondemand_url, exc,
            )
            raise AlertBridgeError(
                f"Failed to reach Alert Bridge at {self.ondemand_url}: {exc}"
            ) from exc

        # Parse the body once — we use it both on the success and error paths.
        body: Dict[str, Any]
        try:
            body = response.json() if response.text else {}
        except ValueError:
            body = {"raw": response.text}

        if response.status_code == 202:
            correlation_id = body.get("correlationId")
            logger.info(
                "✓ Submitted ondemand verification: category=%s correlationId=%s",
                message.get("category"), correlation_id,
            )
            return body

        # Translate AB's documented 400 envelope into a typed exception.
        if response.status_code == 400 and isinstance(body, dict):
            error_code = body.get("error")
            error_msg = body.get("message", "")
            if error_code == "unknown_category":
                logger.error(
                    "Alert Bridge rejected category=%s as unknown — make sure "
                    "the alert_type is seeded in AB's alert_type_config.json: %s",
                    message.get("category"), error_msg,
                )
                raise UnknownCategoryError(error_msg or error_code)
            raise AlertBridgeError(
                f"Alert Bridge 400 {error_code or ''}: {error_msg or body}"
            )

        logger.error(
            "Alert Bridge returned HTTP %s for ondemand request: %s",
            response.status_code, body,
        )
        raise AlertBridgeError(
            f"Alert Bridge returned HTTP {response.status_code}: {body}"
        )

    def submit_incident(self, incident: Dict[str, Any]) -> Dict[str, Any]:
        """Submit an NvSchema incident to Alert Agent's Kafka-backed ingress.

        ``POST /api/v1/incidents`` is still an HTTP API, but it is the public
        ingress into the message-bus path: Alert Agent converts the JSON to its
        Incident protobuf and publishes it to the configured Kafka incident
        topic. Synthetic latency eval uses this path so the normal worker
        latency metrics are emitted.
        """
        try:
            response = self._session.post(
                self.incident_url,
                json=incident,
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            logger.error(
                "POST %s failed at the transport layer: %s",
                self.incident_url, exc,
            )
            raise AlertBridgeError(
                f"Failed to reach Alert Bridge at {self.incident_url}: {exc}"
            ) from exc

        try:
            body = response.json() if response.text else {}
        except ValueError:
            body = {"raw": response.text}

        if response.status_code in {200, 201, 202}:
            logger.info(
                "✓ Submitted synthetic incident to message bus: category=%s id=%s",
                incident.get("category"), incident.get("id"),
            )
            return body if isinstance(body, dict) else {"raw": body}

        logger.error(
            "Alert Bridge returned HTTP %s for incident request: %s",
            response.status_code, body,
        )
        raise AlertBridgeError(
            f"Alert Bridge returned HTTP {response.status_code} for incident request: {body}"
        )

    # ------------------------------------------------------------------
    # Verification config CRUD (`/api/v1/verification/config`)
    # ------------------------------------------------------------------
    #
    # These methods cover the "evaluator owns the prompt config lifecycle"
    # flow — usually called once at startup (seed) and once at teardown
    # (cleanup). They are not on the per-incident hot path.
    #
    # Endpoints (see ``alert-agent-web/app/api/alert_config_routes.py``):
    #   POST   /api/v1/verification/config              -> 201 / 409
    #   GET    /api/v1/verification/config/{alert_type} -> 200 / 404
    #   PUT    /api/v1/verification/config/{alert_type} -> 200 / 404
    #   DELETE /api/v1/verification/config/{alert_type} -> 200 / 404
    # ------------------------------------------------------------------

    @property
    def _config_base_url(self) -> str:
        return f"{self._base_url}/api/v1/verification/config"

    def get_alert_config(self, alert_type: str) -> Optional[Dict[str, Any]]:
        """Fetch a single alert config from AB's REST CRUD API.

        Used as an optional preflight check ("is this category seeded?") before
        running a batch — not on the hot path. Returns ``None`` on 404.
        """
        try:
            response = self._session.get(
                f"{self._config_base_url}/{alert_type}",
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.warning(
                "Alert config preflight check failed for alert_type=%s: %s",
                alert_type, exc,
            )
            return None
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            logger.warning(
                "Alert config preflight check returned HTTP %s for %s",
                response.status_code, alert_type,
            )
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def create_alert_config(
        self,
        alert_type: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        enrichment_prompt: Optional[str] = None,
        vlm_params: Optional[Dict[str, Any]] = None,
        output_category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create an alert_type config in Alert Bridge.

        Mirrors ``AlertConfigRequest`` upstream: ``alert_type`` and ``prompt``
        are required; the other fields are passed through only when provided
        so we don't accidentally overwrite an upstream default with ``None``.

        Returns the parsed 201 body, e.g.::

            {"alert_type": "...", "created_at": "...", "updated_at": "..."}

        Raises:
            AlertBridgeError: on 409 (already exists), 422 (invalid payload),
                or any transport / 5xx failure. Use :meth:`upsert_alert_config`
                if you want 409 to fall back to PUT automatically.
        """
        payload: Dict[str, Any] = {
            "alert_type": alert_type,
            "prompt": prompt,
        }
        if system_prompt is not None:
            payload["system_prompt"] = system_prompt
        if enrichment_prompt is not None:
            payload["enrichment_prompt"] = enrichment_prompt
        if vlm_params is not None:
            payload["vlm_params"] = vlm_params
        if output_category is not None:
            payload["output_category"] = output_category

        return self._config_request(
            method="POST",
            url=self._config_base_url,
            payload=payload,
            expected=201,
            ctx=f"create alert_type='{alert_type}'",
        )

    def update_alert_config(
        self,
        alert_type: str,
        prompt: Optional[str] = None,
        system_prompt: Optional[str] = None,
        enrichment_prompt: Optional[str] = None,
        vlm_params: Optional[Dict[str, Any]] = None,
        output_category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Partially update an alert_type config (PUT).

        AB applies a deep merge for ``vlm_params`` — top-level fields are
        replaced; missing fields are left unchanged. Only fields explicitly
        passed by the caller are forwarded.
        """
        payload: Dict[str, Any] = {}
        if prompt is not None:
            payload["prompt"] = prompt
        if system_prompt is not None:
            payload["system_prompt"] = system_prompt
        if enrichment_prompt is not None:
            payload["enrichment_prompt"] = enrichment_prompt
        if vlm_params is not None:
            payload["vlm_params"] = vlm_params
        if output_category is not None:
            payload["output_category"] = output_category
        if not payload:
            raise AlertBridgeError(
                f"update_alert_config('{alert_type}') called with no fields to update"
            )

        return self._config_request(
            method="PUT",
            url=f"{self._config_base_url}/{alert_type}",
            payload=payload,
            expected=200,
            ctx=f"update alert_type='{alert_type}'",
        )

    def upsert_alert_config(
        self,
        alert_type: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        enrichment_prompt: Optional[str] = None,
        vlm_params: Optional[Dict[str, Any]] = None,
        output_category: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        """Create-or-update: try POST first, fall back to PUT on 409.

        Returns ``(body, was_updated)`` where ``was_updated`` is ``True`` iff
        we hit the PUT-fallback branch (i.e. the slug already existed in AB).
        Returning the outcome explicitly avoids the previous log-sentinel
        hack which raced under the parallel seed pool.

        This is the preferred entry point for the seed-on-startup flow because
        it is idempotent across re-runs against an already-seeded AB instance
        (e.g. after a partial previous run, or when the static
        ``alert_type_config.json`` already had the slug).
        """
        try:
            body = self.create_alert_config(
                alert_type=alert_type,
                prompt=prompt,
                system_prompt=system_prompt,
                enrichment_prompt=enrichment_prompt,
                vlm_params=vlm_params,
                output_category=output_category,
            )
            return body, False
        except AlertBridgeError as exc:
            # `_config_request` formats 409 as "Alert Bridge 409 ...".  The
            # safest signal is the substring " 409 " — AB's body also uses
            # error="config_exists" but PUT works regardless of the exact
            # cause, so we don't introspect further.
            if " 409 " not in str(exc):
                raise
            logger.info(
                "alert_type='%s' already exists, falling back to PUT update",
                alert_type,
            )
            body = self.update_alert_config(
                alert_type=alert_type,
                prompt=prompt,
                system_prompt=system_prompt,
                enrichment_prompt=enrichment_prompt,
                vlm_params=vlm_params,
                output_category=output_category,
            )
            return body, True

    def delete_alert_config(self, alert_type: str) -> bool:
        """Delete an alert_type config.

        Returns ``True`` on 200 (deleted) and ``False`` on 404 (already gone)
        so cleanup loops stay idempotent. Any other status raises
        :class:`AlertBridgeError`.
        """
        url = f"{self._config_base_url}/{alert_type}"
        try:
            response = self._session.delete(url, timeout=10)
        except requests.RequestException as exc:
            raise AlertBridgeError(
                f"Failed to reach Alert Bridge at {url}: {exc}"
            ) from exc
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        raise AlertBridgeError(
            f"Alert Bridge returned HTTP {response.status_code} for "
            f"DELETE {url}: {response.text}"
        )

    def _config_request(
        self,
        method: str,
        url: str,
        payload: Dict[str, Any],
        expected: int,
        ctx: str,
    ) -> Dict[str, Any]:
        """Shared helper for POST/PUT against the config endpoints."""
        try:
            response = self._session.request(
                method=method,
                url=url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise AlertBridgeError(
                f"Failed to reach Alert Bridge at {url}: {exc}"
            ) from exc
        try:
            body = response.json() if response.text else {}
        except ValueError:
            body = {"raw": response.text}
        if response.status_code == expected:
            logger.debug("✓ %s -> HTTP %s (%s)", ctx, response.status_code, body)
            return body if isinstance(body, dict) else {"raw": body}
        # Note the leading/trailing spaces around the status code: callers
        # (notably `upsert_alert_config`) rely on " 409 " to detect conflicts.
        raise AlertBridgeError(
            f"Alert Bridge {response.status_code} for {ctx}: {body}"
        )

    def close(self) -> None:
        """Close the calling thread's HTTP session.

        Sessions belonging to other threads are not touched — they will be
        garbage-collected when those threads exit. This is fine for our
        usage (process-wide singleton client), and avoids the alternative
        of tracking every TLS-allocated session in a global registry.
        """
        s = getattr(self._tls, "session", None)
        if s is not None:
            try:
                s.close()
            finally:
                self._tls.session = None
