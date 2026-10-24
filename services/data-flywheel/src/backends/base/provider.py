"""``BackendProvider`` — the single seam every backend implements.

The provider is a factory: handlers and library code obtain backend objects
through it (``provider.training()``, ``provider.evaluator(...)``, …) instead of
importing a backend SDK directly. One provider is selected at runtime by
``backend.kind`` (see ``src/backends/factory.py``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.backends.base.interfaces import (
    DatastoreBackend,
    DeploymentClient,
    EmbeddingBackend,
    EvalBackend,
    TrainingBackend,
)


class BackendProvider(ABC):
    """Abstract factory of per-concern backend objects.

    This base declares only the **backend-neutral** contract — the method names
    and the parameters every backend shares. Backend-specific construction
    options and defaults (e.g. NMP's evaluator tool/nvext flags, or its default
    embedding model) live on the concrete provider's override, not here.
    """

    #: short identifier, e.g. "nmp" or "deft"
    kind: str = "base"

    #: Job types this backend can run. Gated at the API edge (``/jobs``,
    #: ``/configs``) and the task dispatcher. Empty by default (fail-closed) — a
    #: concrete backend opts in explicitly (e.g. NMP -> ``{"llm_job",
    #: "embedding_job"}``). Values are the ``JobType`` identifiers.
    supported_job_types: frozenset[str] = frozenset()

    def supports_job_type(self, job_type: str) -> bool:
        """Whether this backend can run ``job_type`` (override for custom logic)."""
        return job_type in self.supported_job_types

    # NOTE: building a typed JobConfig from a config_type is NOT here — the config
    # schemas are the shared contract (``src/config``), so ``config_manager`` builds
    # them directly via ``src.config.build_job_config``. The provider only declares
    # which job types it *runs* (``supported_job_types``); gating is at the API edge.

    @abstractmethod
    def training(self) -> TrainingBackend:
        """Training/customization backend (NMP: ``Customizer``)."""

    @abstractmethod
    def evaluator(self) -> EvalBackend | None:
        """Evaluation backend — an ``EvalBackend``.

        The base signature is neutral (no args). Each backend adds its own
        construction kwargs on the override: NMP adds ``judge_model_config`` /
        ``include_tools`` / ``include_nvext`` (all LLM-judge / tool-calling
        concerns) and returns its service-style ``Evaluator``; DEFT adds
        ``use_case`` and returns a ``DeftAOIEvaluator`` that implements both
        ``EvalBackend`` and the harness ``BaseEvaluator``. May return ``None``
        when the backend has no evaluator to operate on for a given call (e.g.
        DEFT's cleanup/cancel path, which passes no ``use_case``).
        """

    @abstractmethod
    def datastore(self, dataset_name: str) -> DatastoreBackend:
        """Dataset upload/registration backend (NMP: ``DataUploader``)."""

    @abstractmethod
    def deployment_client(self, nim: Any) -> DeploymentClient:
        """Model deployment client (NMP: ``DMSClient``)."""

    @abstractmethod
    def embedding(
        self,
        endpoint_url: str | None = None,
        model_name: str | None = None,
        api_key: str | None = None,
    ) -> EmbeddingBackend:
        """Embedding client (NMP: ``Embedding``).

        The default ``model_name`` is backend-specific and supplied by the
        concrete provider's override when ``None`` is passed.
        """

    def validate_llm_judge(self) -> bool:
        """Validate the configured LLM judge is reachable/usable.

        Optional hook — **not** abstract: the LLM judge is an NMP-only concern, so
        the default is a no-op (``True``) for backends that don't use one. The judge
        *config* is global (``settings.system_config.llm_judge_config``); callers read
        it from settings directly. NMP overrides this to do the real reachability check.
        """
        return True
