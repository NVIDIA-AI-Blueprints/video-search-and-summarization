"""
Business logic services for Alert Bridge Evaluator.

This module intentionally avoids eager imports so callers can import a single
service submodule without loading optional runtime dependencies for all
services (for example `redis` required by the job service, or `prometheus_api_client`
required by the Prometheus client).
"""

from importlib import import_module

# Public attribute name -> (relative module, attr in that module). Keeping the
# top-level namespace flat lets callers write ``from services import X`` without
# knowing which submodule defines ``X``; the ``__getattr__`` hook below resolves
# the actual import only when ``X`` is first looked up, so importing a single
# service doesn't drag in the others' heavy/optional dependencies.
_EXPORTS = {
    # Core services
    "JobService": (".jobs", "JobService"),
    "EvaluationService": (".evaluation", "EvaluationService"),
    "ReportService": (".reports", "ReportService"),
    # Alert Bridge client + its typed errors (api_align)
    "AlertBridgeClient": (".alert_bridge_client", "AlertBridgeClient"),
    "AlertBridgeError": (".alert_bridge_client", "AlertBridgeError"),
    "UnknownCategoryError": (".alert_bridge_client", "UnknownCategoryError"),
    # Elasticsearch + Prometheus result sources
    "ElasticsearchClient": (".elasticsearch_client", "ElasticsearchClient"),
    "PrometheusClient": (".prometheus_client", "PrometheusClient"),
    "PrometheusError": (".prometheus_client", "PrometheusError"),
    # Category / alert_type slug resolution helpers (api_align).
    # ``gt_row_slug`` is the row-stable resolver added in the
    # duplicate-video fix — exposing it at the package level keeps the
    # seed/submit slug contract discoverable for tests and downstream
    # callers.
    "gt_entry_to_alert_type": (".category_resolver", "gt_entry_to_alert_type"),
    "gt_row_slug": (".category_resolver", "gt_row_slug"),
    "slugify_alert_type": (".category_resolver", "slugify_alert_type"),
}

__all__ = list(_EXPORTS.keys())


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
