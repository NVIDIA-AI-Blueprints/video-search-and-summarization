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
"""DEFT evaluators — the backend owns its use-case eval logic.

The generic Eval Harness (``src/lib/eval_harness``) orchestrates (collect →
evaluate → verdict / offline gate); the *metric logic* is backend- and
use-case-specific and lives here. The harness is handed one of these evaluators
via ``get_backend_provider().evaluator(use_case=...)`` — it never reaches back
into a backend.

Adding a new use case (e.g. ``pas``) is a new ``BaseEvaluator`` subclass + one
entry in ``_EVALUATORS`` — no change to the harness, the provider seam, or NMP.

**Skeleton:** metric bodies are stubs (empty metrics / fail-closed verdicts).
"""

from __future__ import annotations

from typing import Any, ClassVar

from src.backends.base.interfaces import EvalBackend
from src.backends.deft.online_aoi_samples import (
    GOOD,
    NOT_GOOD,
    ground_truth,
    records_to_samples,
)
from src.lib.eval_harness.base import BaseEvaluator
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.backends.deft.eval")

# Comparator table for the dict form of a KPI constraint (the structured
# ``KpiConstraint`` model carries its own ``check``; this is the defensive
# fallback when constraints arrive as plain dicts).
_OPS: dict[str, Any] = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
}

# Markers the ITS offline gate appends to a constraint it could not EVALUATE, so
# "the candidate missed the bar" reads differently from "the config names a
# metric nothing emits" — the latter rejects every candidate indefinitely. AOI's
# gate deliberately does not carry them; its string is unchanged.
_REASON_METRIC_NOT_REPORTED = "metric-not-reported"
_REASON_UNKNOWN_OPERATOR = "unknown-operator"


class DeftEvaluator(EvalBackend, BaseEvaluator):
    """Base for DEFT use-case evaluators — implements **both** contracts.

    - ``EvalBackend`` (``get_status`` / ``get_results`` / ``cancel``): the neutral
      provider seam, so ``provider.evaluator()`` returns a real ``EvalBackend``.
      DEFT eval is synchronous/in-process, so these service verbs are trivial
      shims (no remote eval job) — shared by all use cases, defined here once.
    - ``BaseEvaluator`` (``collect`` / ``evaluate`` / ``kpi_eval``): the harness
      contract — left abstract here; each use-case subclass implements the
      metrics, so one edit cannot move two gates.
    """

    #: Legacy ``eval_harness.thresholds`` seed, superseded by
    #: ``EvalHarnessConfig.online_target``; kept only so a legacy FAR entry still
    #: gates. Overridable per use case.
    DEFAULT_THRESHOLDS: ClassVar[dict[str, float]] = {}

    #: Minimum collected evidence before a non-``hold`` verdict; WHAT is counted
    #: is the subclass's call.
    DEFAULT_MIN_SAMPLE_SIZE: ClassVar[int] = 100

    def __init__(
        self,
        results_source: Any = None,
        thresholds: dict[str, float] | None = None,
        min_sample_size: int | None = None,
    ) -> None:
        """Construct a DEFT evaluator.

        All params default so ``get_evaluator(use_case)`` can build one with no
        args (the provider-seam contract).

        Args:
            results_source: Optional callable/collection used by ``collect``
                to obtain samples for a ``(window, role)`` (test seam / future
                object-store adapter). If it is callable it is invoked as
                ``results_source(window, role)``; otherwise it is treated as a
                pre-collected iterable of sample dicts. ``None`` -> ``collect``
                returns ``[]``.
            thresholds: Metric -> threshold overrides; merged over
                ``DEFAULT_THRESHOLDS``.
            min_sample_size: Minimum samples before a non-``hold`` verdict.
        """
        self._results_source = results_source
        self.thresholds: dict[str, float] = {**self.DEFAULT_THRESHOLDS, **(thresholds or {})}
        self.min_sample_size: int = (
            self.DEFAULT_MIN_SAMPLE_SIZE if min_sample_size is None else int(min_sample_size)
        )
        #: ``EvalHarnessConfig.online_target``, set per promotion by the beat.
        #: Empty means fail-closed (hold).
        self.online_target: list[Any] = []

    # --- EvalBackend: neutral seam verbs (in-process shims, shared) ---
    def get_status(self, job_id: str) -> dict[str, Any]:
        # DEFT eval is synchronous/in-process — there is no async job to track.
        return {"status": "completed"}

    def get_results(self, job_id: str) -> dict[str, Any]:
        # Results are produced directly by evaluate()/kpi_eval(); no job store.
        return {"metrics": {}}

    def cancel(self, job_id: str) -> None:
        # Nothing to cancel — no remote eval job.
        return None


#: Back-compat aliases for the constants that became ``DeftEvaluator`` ClassVars,
#: kept so the old private names still import.
_DEFAULT_AOI_THRESHOLDS: dict[str, float] = DeftEvaluator.DEFAULT_THRESHOLDS
_DEFAULT_MIN_SAMPLE_SIZE: int = DeftEvaluator.DEFAULT_MIN_SAMPLE_SIZE


class DeftAOIEvaluator(DeftEvaluator):
    """Evaluator for AOI / Visual-ChangeNet defect-detection models.

    Implements the harness ``BaseEvaluator`` contract with AOI metric logic.
    The online gate is FAR-only; the offline gate verifies the
    candidate's DEFT-reported metrics against the structured KPI acceptance
    criteria (``EvalHarnessConfig.kpi_target`` — a list of ``metric/op/threshold``
    constraints). There is no DFW-side baseline comparison — DEFT returns
    candidate-only metrics and handles the baseline inside its own loop
    (``baseline_eval`` pre-loop step). The generic ``OfflineEval`` wrapper is a
    thin delegate and knows nothing about metric names or comparators.

    Assumed collected-sample schema (per the AOI secondary-inference result, see
    ``aoi_client`` ``SecondaryInspectionTargetResult``); each sample is a dict::

        {
          "target_id": str,
          "ai_classification": "Good" | "NotGood",  # candidate model's call
          "ai_confidence": float,                    # [0, 1]
          "ai_defect_type": str | None,              # optional
          "machine_reported_defect_type": str | None,  # optional
          "confirmed_label": "Good" | "NotGood" | None,  # operator ground truth
          "baseline_classification": "Good" | "NotGood" | None,  # deployed model
        }

    ``confirmed_label`` (operator-confirmed ground truth) and
    ``baseline_classification`` (the currently-deployed model's call for the same
    target) are only present for a subset of targets; every metric is computed
    over whichever subset has the data it needs (documented per metric below).
    """

    # --- BaseEvaluator: the AOI eval logic (run by the harness) ---
    def collect(self, window: Any, role: str) -> list[dict]:
        """Collect candidate samples for ``window``/``role``.

        Reads ``self._results_source`` (the eval-set flow in production) and
        flattens its board records into per-target samples.
        """
        if self._results_source is None:
            logger.debug(
                "DeftAOIEvaluator.collect: no results_source; returning [] (role=%s)", role
            )
            return []
        source = self._results_source
        records = source(window, role) if callable(source) else source
        samples = records_to_samples(records)
        logger.debug(
            "collect: role=%s -> %d record(s) -> %d target sample(s)",
            role,
            len(list(records)) if isinstance(records, list) else -1,
            len(samples),
        )
        return samples

    def evaluate(self, baseline: Any, candidate: Any, sample: Any) -> dict:
        """Decide the rollout verdict from FAR alone — the one metric the DEFT
        loop converges on (``app/execution/kpi.py``).

        ``hold`` when the PASS population is under ``min_sample_size`` or no
        criteria are configured (fail-closed); ``advance`` when every constraint
        is met; ``rollback`` when any is violated.
        """
        samples: list[dict] = list(sample or [])
        metrics = self._online_metrics(samples)

        # The PASS population is the FAR denominator, so it is what must be
        # big enough — not the raw record count.
        n_pass = int(metrics.get("n_pass", 0))
        if n_pass < self.min_sample_size:
            logger.debug(
                "evaluate: n_pass=%d < min_sample_size=%d -> hold", n_pass, self.min_sample_size
            )
            return {"metrics": metrics, "verdict": "hold"}

        constraints = self._online_constraints()
        if not constraints:
            logger.warning(
                "No online criteria configured (eval_harness.online_target); holding. "
                "Set e.g. [{metric: FAR_pct, op: '<', threshold: 0.1}]."
            )
            return {"metrics": metrics, "verdict": "hold"}

        # Same constraint engine as the offline gate, so the two cannot drift.
        unmet = self._unmet_constraints(metrics, {"kpi_target": constraints})
        if unmet:
            logger.info("Online FAR constraints not met: %s", unmet)
            return {"metrics": metrics, "verdict": "rollback", "unmet": unmet}
        return {"metrics": metrics, "verdict": "advance"}

    def _online_constraints(self) -> list[Any]:
        """``online_target``, else a legacy ``thresholds`` FAR entry read as
        ``FAR_pct < value``. Other legacy keys are ignored — only FAR gates.

        The legacy keys held FRACTIONS (the shipped default was
        ``false_accept_rate: 0.001``, meaning 0.1%), so they are scaled to the
        percentage ``FAR_pct`` is expressed in; a ``*_pct`` key already is one.
        """
        if self.online_target:
            return list(self.online_target)
        thresholds = self.thresholds or {}
        for key, scale in (
            ("FAR_pct", 1.0),
            ("far_pct", 1.0),
            ("far", 100.0),
            ("false_accept_rate", 100.0),
        ):
            if key in thresholds:
                return [{"metric": "FAR_pct", "op": "<", "threshold": thresholds[key] * scale}]
        return []

    def kpi_eval(self, baseline: Any, candidate: Any, kpi_set: Any) -> dict:
        """Offline KPI gate: verify the candidate's DEFT metrics against the criteria.

        Assumed DEFT result payload shape (per ``ResultsResponse`` in
        ``deft_api/openapi.json`` — read defensively; missing keys are simply
        absent from the returned metrics)::

            {"metrics": {"mAP50": float, "far": float, "recall": float, ...}}

        ``candidate`` may be that payload, a bare metrics dict, or ``None``.
        ``kpi_set`` is the ``EvalHarnessConfig`` (or a dict) carrying the
        structured ``kpi_target`` constraints. ``passed`` is true iff every
        constraint is satisfied; ``unmet`` constraints are returned for logging.

        ``baseline`` is deliberately IGNORED: the DEFT contract returns
        candidate-only KPI metrics (there is no baseline in ``ResultsResponse``),
        and DEFT's own workflow runs a ``baseline_eval`` pre-loop step — baseline
        consideration is DEFT's job inside the loop. A DFW-side cross-generation
        comparison would compare numbers possibly measured on different KPI-set
        versions, so it was removed.
        """
        candidate_metrics = self._extract_deft_metrics(candidate)

        unmet = self._unmet_constraints(candidate_metrics, kpi_set)
        result = {"metrics": candidate_metrics, "passed": not unmet}
        if unmet:
            logger.info("Offline KPI constraints not met: %s", unmet)
            result["unmet"] = unmet
        return result

    # --- metric helpers -------------------------------------------------------
    @staticmethod
    def _online_metrics(samples: list[dict]) -> dict[str, Any]:
        """Compute the online AOI metrics; ``FAR_pct`` is the decisive one.

        ``FAR_pct`` is the false-ALARM rate — of the targets whose ground truth is
        good, the percentage the candidate flagged — matching DEFT's definition
        and the offline gate's units. Measured at the deployed threshold; there is
        nothing to sweep online.

        Samples the candidate never scored are excluded, not counted as ``"Good"``
        (routine under canary and shadow). Ground truth is the operator's
        ``confirmed_label`` else the AOI reference label; targets with neither are
        excluded.

        ``recall_pct``, ``precision_pct``, ``agreement`` (over ``paired_n``) and
        ``mean_confidence`` are returned for visibility and do **not** gate.
        """
        n = len(samples)
        metrics: dict[str, Any] = {"n": n, "n_pass": 0, "n_defect": 0, "labeled_n": 0}
        if n == 0:
            return metrics

        confidences = [
            s["ai_confidence"] for s in samples if isinstance(s.get("ai_confidence"), int | float)
        ]
        if confidences:
            metrics["mean_confidence"] = sum(confidences) / len(confidences)

        # Over samples BOTH models scored: requiring only the baseline call made
        # a stable-routed canary sample a disagreement, driving agreement to 0.
        agree_pairs = [
            (s.get("ai_classification"), s.get("baseline_classification"))
            for s in samples
            if s.get("baseline_classification") is not None
            and s.get("ai_classification") is not None
        ]
        metrics["paired_n"] = len(agree_pairs)
        if agree_pairs:
            agree = sum(1 for cand, base in agree_pairs if cand == base)
            metrics["agreement"] = agree / len(agree_pairs)

        pass_units: list[dict] = []
        defect_units: list[dict] = []
        for s in samples:
            if s.get("ai_classification") is None:
                continue
            truth = ground_truth(s)
            if truth == GOOD:
                pass_units.append(s)
            elif truth == NOT_GOOD:
                defect_units.append(s)

        metrics["n_pass"] = len(pass_units)
        metrics["n_defect"] = len(defect_units)
        metrics["labeled_n"] = len(pass_units) + len(defect_units)

        if pass_units:
            false_alarms = sum(1 for s in pass_units if s["ai_classification"] == NOT_GOOD)
            metrics["n_false_alarms"] = false_alarms
            metrics["FAR_pct"] = 100.0 * false_alarms / len(pass_units)

        if defect_units:
            caught = sum(1 for s in defect_units if s["ai_classification"] == NOT_GOOD)
            metrics["n_defects_caught"] = caught
            metrics["recall_pct"] = 100.0 * caught / len(defect_units)
            flagged = caught + metrics.get("n_false_alarms", 0)
            if flagged:
                metrics["precision_pct"] = 100.0 * caught / flagged

        return metrics

    @staticmethod
    def _extract_deft_metrics(payload: Any) -> dict[str, float]:
        """Pull the numeric metrics dict out of a DEFT result payload.

        Accepts the full payload (``{"metrics": {...}}``), a bare metrics dict,
        or ``None``. Only numeric values are kept.
        """
        if payload is None:
            return {}
        raw: Any = payload
        if isinstance(payload, dict) and isinstance(payload.get("metrics"), dict):
            raw = payload["metrics"]
        if not isinstance(raw, dict):
            return {}
        return {k: float(v) for k, v in raw.items() if isinstance(v, int | float)}

    # --- KPI-constraint helpers (the offline gate decision) -------------------
    def _unmet_constraints(self, metrics: dict[str, float], kpi_set: Any) -> list[str]:
        """Return a human-readable list of unmet KPI constraints (empty = all met).

        A constraint whose metric is absent from the candidate metrics counts as
        unmet (fail-closed) — the gate must not silently pass when a required KPI
        metric was never produced.
        """
        unmet: list[str] = []
        for c in self._kpi_constraints(kpi_set):
            metric, op, threshold = self._constraint_fields(c)
            if metric is None or op is None or threshold is None:
                continue
            value = self._lookup_metric(metrics, metric)
            checker = getattr(c, "check", None)
            # Dict-form constraints are unvalidated — an unknown operator counts
            # as unmet (fail-closed) rather than crashing the gate path.
            op_fn = _OPS.get(str(op))
            met = value is not None and (
                checker(value)
                if callable(checker)
                else (op_fn is not None and op_fn(value, threshold))
            )
            if not met:
                unmet.append(f"{metric} {op} {threshold} (actual={value})")
        return unmet

    @staticmethod
    def _kpi_constraints(kpi_set: Any) -> list:
        """Extract the structured ``kpi_target`` constraints from ``kpi_set``."""
        if kpi_set is None:
            return []
        target = getattr(kpi_set, "kpi_target", None)
        if target is None and isinstance(kpi_set, dict):
            target = kpi_set.get("kpi_target")
        return list(target or [])

    @staticmethod
    def _constraint_fields(c: Any) -> tuple[Any, Any, Any]:
        """Pull ``(metric, op, threshold)`` from a ``KpiConstraint`` or dict."""
        if isinstance(c, dict):
            return c.get("metric"), c.get("op"), c.get("threshold")
        return getattr(c, "metric", None), getattr(c, "op", None), getattr(c, "threshold", None)

    @staticmethod
    def _lookup_metric(metrics: dict[str, Any], name: Any) -> float | None:
        """Case-insensitive metric lookup (``FAR`` matches ``far`` etc.)."""
        if name in metrics and isinstance(metrics[name], int | float):
            return float(metrics[name])
        lname = str(name).lower()
        for key, val in metrics.items():
            if key.lower() == lname and isinstance(val, int | float):
                return float(val)
        return None


class DeftITSEvaluator(DeftEvaluator):
    """Evaluator for ITS / real-time streaming object-detection models.

    ``kpi_eval`` (OFFLINE) is ITS's own constraint check over DEFT's free-form
    ``{name: number}`` metrics, deliberately not shared with AOI so neither use
    case can edit the other's promotion decision. ``evaluate`` (ONLINE) always
    holds: there is no agreed ITS online metric contract, and the shipped
    ``its_job_config`` sets ``eval_harness.enabled: false`` (v1 is a shadow soak
    ending at a human approval), so a number here would be a guess.
    """

    def collect(self, window: Any, role: str) -> list[dict]:
        """Return the eval-set records for ``window``/``role``, unflattened.

        No per-record normalisation: the ITS record shape is not agreed yet and a
        wrong guess is silent (an unrecognised key yields a zero count that reads
        downstream as a real measurement of zero). Nothing consumes these today —
        ITS has no online gate.
        """
        if self._results_source is None:
            return []
        source = self._results_source
        records = source(window, role) if callable(source) else source
        return list(records or [])

    def evaluate(self, baseline: Any, candidate: Any, sample: Any) -> dict:
        """Always ``hold`` with empty metrics — ITS has no agreed online metric
        contract yet, so there is nothing honest to score.

        Fail-closed by the same rule as ``DeftAOIEvaluator``'s no-criteria path:
        ``hold`` never promotes (the beat only enqueues ``advance``/``rollback``),
        but it IS a verdict, so the observation-window timeout stays reachable and
        an ``on_timeout: escalate`` policy still fires. Raising here instead would
        abort the poll before the verdict write, and the beat's blanket
        ``except Exception: continue`` would leave the promotion stuck in
        ``promoting`` forever, holding the concurrency-1 parent queue slot.
        """
        logger.warning(
            "ITS has no agreed online metric contract; holding. The shipped "
            "its_job_config sets promotion_policy.eval_harness.enabled=false — "
            "an enabled online gate on an ITS promotion will hold until the "
            "observation window elapses."
        )
        return {"metrics": {}, "verdict": "hold"}

    def kpi_eval(self, baseline: Any, candidate: Any, kpi_set: Any) -> dict:
        """Offline KPI gate: check DEFT's reported metrics against ``kpi_target``.

        ``baseline`` is ignored — DEFT returns candidate-only metrics and runs its
        own ``baseline_eval`` inside the loop. Fail-closed: an unreported metric
        is UNMET and carries a ``reason=`` marker, so a wrong metric key does not
        read as a bad model.
        """
        payload = candidate
        if isinstance(payload, dict) and isinstance(payload.get("metrics"), dict):
            payload = payload["metrics"]
        metrics: dict[str, float] = (
            {
                k: float(v)
                for k, v in payload.items()
                if isinstance(v, int | float) and not isinstance(v, bool)
            }
            if isinstance(payload, dict)
            else {}
        )

        target = getattr(kpi_set, "kpi_target", None)
        if target is None and isinstance(kpi_set, dict):
            target = kpi_set.get("kpi_target")

        unmet: list[str] = []
        for c in target or []:
            if isinstance(c, dict):
                metric, op, threshold = c.get("metric"), c.get("op"), c.get("threshold")
            else:
                metric = getattr(c, "metric", None)
                op = getattr(c, "op", None)
                threshold = getattr(c, "threshold", None)
            if metric is None or op is None or threshold is None:
                continue
            # Case-insensitive: the exact ITS metric spelling is unconfirmed, and
            # a case mismatch would otherwise reject every candidate forever.
            value = metrics.get(metric)
            if value is None:
                lname = str(metric).lower()
                value = next((v for k, v in metrics.items() if k.lower() == lname), None)
            # A validated ``KpiConstraint`` carries its own comparator; the table
            # is the fallback for dict-form constraints, which are unvalidated.
            checker = getattr(c, "check", None)
            op_fn = _OPS.get(str(op))
            if value is None:
                reason = f", reason={_REASON_METRIC_NOT_REPORTED}"
            elif not callable(checker) and op_fn is None:
                reason = f", reason={_REASON_UNKNOWN_OPERATOR}"
            else:
                met = checker(value) if callable(checker) else op_fn(value, threshold)
                if met:
                    continue
                reason = ""
            unmet.append(f"{metric} {op} {threshold} (actual={value}{reason})")

        result: dict[str, Any] = {"metrics": metrics, "passed": not unmet}
        if unmet:
            logger.info("ITS offline KPI constraints not met: %s", unmet)
            result["unmet"] = unmet
        return result


#: Backend-local use-case → evaluator map. Add new use cases (e.g. "pas") here;
#: each value is a DeftEvaluator subclass (both EvalBackend and BaseEvaluator).
_EVALUATORS: dict[str, type[DeftEvaluator]] = {
    "aoi": DeftAOIEvaluator,
    "its": DeftITSEvaluator,
}


def evaluator_for(use_case: str) -> type[DeftEvaluator] | None:
    """Return the evaluator CLASS registered for ``use_case``, or None.

    The public form of "which evaluator scores this use case?" — callers outside
    this module (the provider asks it to decide the AOI family) need the answer,
    not the container, so they must not read ``_EVALUATORS`` directly and pin
    this module to a dict. Unlike :func:`get_evaluator` this neither instantiates
    nor raises: it is a question, and an unregistered use case is a legitimate
    None. Callers must treat that None as "not this family" — "unknown" read as
    "yes" is the direction that grades a rollout on another use case's data.
    """
    return _EVALUATORS.get(use_case)


def get_evaluator(use_case: str, results_source: Any = None) -> DeftEvaluator:
    """Return the concrete DEFT evaluator for ``use_case``.

    ``results_source`` (optional) is the collected-sample reader forwarded to the
    evaluator (see :meth:`DeftAOIEvaluator.collect`) — the provider passes an
    HTTP reader when the deployment configures ``deft_config.aoi_results_url``.
    Raises ``ValueError`` for an unknown use case (so the run fails loudly rather
    than silently evaluating with the wrong metrics).
    """
    try:
        return _EVALUATORS[use_case](results_source=results_source)
    except KeyError as e:
        raise ValueError(
            f"No DEFT evaluator registered for use_case={use_case!r}; "
            f"available: {sorted(_EVALUATORS)}."
        ) from e
