"""Backend selection + provider singleton.

The backend kind is resolved **once** by ``Settings.from_yaml`` (the ``BACKEND``
env var overrides config ``backend.kind``, else defaults to ``"nmp"``) and stored
on ``settings.backend.kind`` — the single source of truth. This factory reads
that value and lazily imports the matching provider, so a build that ships only
one backend never imports the other's module (or SDK).
"""

from __future__ import annotations

from src.backends.base.provider import BackendProvider

_provider: BackendProvider | None = None


def get_backend_kind() -> str:
    """Return the active backend kind from ``settings.backend.kind``.

    That value is already the env-overridable result resolved in
    ``Settings.from_yaml`` — reading it here (rather than re-checking ``BACKEND``)
    keeps the provider in lock-step with the ``system_config`` / ``job_configs``
    that were loaded for the same kind. ``settings`` is always importable by the
    time this runs (it's built at ``src.config`` import), so an import failure is a
    real error and is allowed to surface rather than silently defaulting to nmp.
    """
    from src.config import settings

    kind = getattr(getattr(settings, "backend", None), "kind", None)
    return str(kind).strip().lower() if kind else "nmp"


def _build(kind: str) -> BackendProvider:
    if kind == "nmp":
        from src.backends.nmp.provider import NmpBackendProvider

        return NmpBackendProvider()
    if kind == "deft":
        # Imported only when selected — keeps NMP-only builds free of DEFT code.
        from src.backends.deft.provider import DeftBackendProvider

        return DeftBackendProvider()
    raise ValueError(f"Unknown backend kind: {kind!r} (expected 'nmp' or 'deft')")


def get_backend_provider() -> BackendProvider:
    """Return the process-wide backend provider (built on first use)."""
    global _provider
    if _provider is None:
        _provider = _build(get_backend_kind())
    return _provider


def set_backend_provider(provider: BackendProvider | None) -> None:
    """Inject or reset the provider singleton (test hook)."""
    global _provider
    _provider = provider
