"""Test bootstrap: point the SQLite store at a throwaway per-session file
BEFORE any app module import, so test runs are hermetic (a repo-local
deft_service.db from a previous session must never leak idempotency keys or
runs into the suite)."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="deft-service-tests-")
os.environ.setdefault("DEFT_DB_PATH", os.path.join(_tmp, "test.db"))
os.environ.setdefault("DEFT_EXECUTION_MODE", "simulate")
