"""
Lifecycle hooks for the evaluator service.

Used by the FastAPI startup event in ``main.py`` to:

* wait for Alert Bridge to be reachable before seeding;
* seed AB's prompt registry from the GT file so every ``alert_type`` the
  evaluator sends to ``/api/v1/verification/ondemand`` is resolvable on
  AB's side without an out-of-band edit to ``alert_type_config.json``.
"""

from .seed_ab import SeedSummary, seed_alert_types_from_gt, wait_for_alert_bridge

__all__ = [
    "SeedSummary",
    "seed_alert_types_from_gt",
    "wait_for_alert_bridge",
]
