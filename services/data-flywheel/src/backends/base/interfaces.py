"""Backend-neutral capability contracts (the seam's base interface).

Each concern is an **ABC defining the *basic* interface** every backend must
provide, in backend-neutral verbs. A backend's concrete class **derives** from
the relevant ABC, implements these basics, and is free to **add more** of its
own (backend-specific) methods — e.g. NMP's ``Customizer`` exposes
``create_target`` / ``wait_for_model_sync`` that are not part of this contract.

Design notes:
- **Required basics are ``@abstractmethod``** → a backend that omits one can't be
  instantiated (fails loudly at construction). No ``NotImplementedError`` stubs
  needed: the base holds only methods every backend has; extras live on derived
  classes, so they're never reached on a backend that lacks them.
- **Generic / orchestration code depends only on these basics.** Backend-specific
  pipelines may use the richer derived type directly.
- Nothing here imports a backend SDK. Signatures stay permissive
  (``*args/**kwargs``) until a second backend (DEFT) lets us converge them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class TrainingBackend(ABC):
    """Fine-tuning / customization.  NMP: ``Customizer`` · DEFT: DEFT Service."""

    @abstractmethod
    def start_run(self, *args: Any, **kwargs: Any) -> Any:
        """Launch a training/customization run; return its handle/id(s)."""

    @abstractmethod
    def get_status(self, run_id: str) -> dict[str, Any]:
        """Return the current status of a run."""

    @abstractmethod
    def cancel(self, run_id: str) -> None:
        """Cancel a run."""

    @abstractmethod
    def fetch_artifact(self, *args: Any, **kwargs: Any) -> Any:
        """Return the produced model/checkpoint reference (+ metrics)."""


class EvalBackend(ABC):
    """Evaluation.  NMP: ``Evaluator`` · DEFT: Eval Harness."""

    @abstractmethod
    def evaluate(self, *args: Any, **kwargs: Any) -> Any:
        """Start an evaluation (baseline vs candidate)."""

    @abstractmethod
    def get_status(self, job_id: str) -> dict[str, Any]:
        """Return evaluation status."""

    @abstractmethod
    def get_results(self, job_id: str) -> dict[str, Any]:
        """Return evaluation metrics/results."""

    @abstractmethod
    def cancel(self, job_id: str) -> None:
        """Cancel / delete an evaluation job (stop in-flight work, free resources)."""


class DatastoreBackend(ABC):
    """Dataset persistence.  NMP: ``DataUploader`` · DEFT: Object Storage / AL."""

    @abstractmethod
    def put(self, *args: Any, **kwargs: Any) -> Any:
        """Persist dataset content to the backing store."""

    @abstractmethod
    def register(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Register the dataset so a training/eval run can reference it."""

    @abstractmethod
    def get_uri(self) -> str:
        """Return the reference/URI of the persisted dataset."""

    @abstractmethod
    def delete(self) -> None:
        """Remove the dataset from the store."""


class DeploymentClient(ABC):
    """Model deployment.  NMP: ``DMSClient`` · DEFT: Infer Model Management."""

    @abstractmethod
    def deploy(self, *args: Any, **kwargs: Any) -> Any:
        """Deploy/serve a model version."""

    @abstractmethod
    def get_status(self, *args: Any, **kwargs: Any) -> Any:
        """Return deployment status."""

    @abstractmethod
    def wait(self, *args: Any, **kwargs: Any) -> Any:
        """Block until the deployment is ready (or times out)."""

    @abstractmethod
    def teardown(self, *args: Any, **kwargs: Any) -> Any:
        """Tear down / shut down a deployment."""


class EmbeddingBackend(ABC):
    """Embeddings.  NMP: ``Embedding``."""

    @abstractmethod
    def embed(self, *args: Any, **kwargs: Any) -> Any:
        """Return embedding(s) for the given input(s)."""
