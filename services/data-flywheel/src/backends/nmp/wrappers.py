"""Light NMP wrappers — derive from the base ABCs, delegate to the concrete
NMP classes, and forward everything else through (``__getattr__``).

This realizes "base class = basic interface, derived class provides more":
each wrapper implements the neutral basics by delegating to the wrapped NMP
object, and any NMP-specific method the NMP/LLM pipeline still calls
(``start_training_job``, ``get_job_uri``, ``create_target``, …) is forwarded to
the wrapped instance unchanged. The concrete NMP classes stay untouched.
"""

from __future__ import annotations

from typing import Any

from src.backends.base.interfaces import (
    DatastoreBackend,
    DeploymentClient,
    EmbeddingBackend,
    EvalBackend,
    TrainingBackend,
)


class _Wrapper:
    """Hold a concrete impl and forward unknown attributes to it."""

    def __init__(self, impl: Any) -> None:
        self._impl = impl

    def __getattr__(self, name: str) -> Any:
        # Only reached for names not defined on the wrapper itself; `_impl` is in
        # the instance __dict__ so this never recurses.
        return getattr(self._impl, name)


class NmpTraining(TrainingBackend, _Wrapper):
    def start_run(self, *args: Any, **kwargs: Any) -> Any:
        return self._impl.start_training_job(*args, **kwargs)

    def get_status(self, run_id: str) -> dict[str, Any]:
        return self._impl.get_job_status(run_id)

    def cancel(self, run_id: str) -> None:
        return self._impl.cancel_job(run_id)

    def fetch_artifact(self, *args: Any, **kwargs: Any) -> Any:
        return self._impl.get_customized_model_info(*args, **kwargs)


class NmpEval(EvalBackend, _Wrapper):
    def evaluate(self, *args: Any, **kwargs: Any) -> Any:
        return self._impl.run_evaluation(*args, **kwargs)

    def get_status(self, job_id: str) -> dict[str, Any]:
        return self._impl.get_evaluation_status(job_id)

    def get_results(self, job_id: str) -> dict[str, Any]:
        return self._impl.get_evaluation_results(job_id)

    def cancel(self, job_id: str) -> None:
        return self._impl.delete_evaluation_job(job_id)


class NmpDatastore(DatastoreBackend, _Wrapper):
    def put(self, *args: Any, **kwargs: Any) -> Any:
        return self._impl.upload_data(*args, **kwargs)

    def register(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._impl.register_dataset(*args, **kwargs)

    def get_uri(self) -> str:
        return self._impl.get_file_uri()

    def delete(self) -> None:
        # delete_dataset() is a 26.03 compatibility no-op (fileset + files share a
        # lifecycle); unregister_dataset() performs the actual fileset deletion.
        return self._impl.unregister_dataset()


class NmpDeploymentClient(DeploymentClient, _Wrapper):
    def deploy(self, *args: Any, **kwargs: Any) -> Any:
        return self._impl.deploy_model(*args, **kwargs)

    def get_status(self, *args: Any, **kwargs: Any) -> Any:
        return self._impl.get_deployment_status(*args, **kwargs)

    def wait(self, *args: Any, **kwargs: Any) -> Any:
        return self._impl.wait_for_deployment(*args, **kwargs)

    def teardown(self, *args: Any, **kwargs: Any) -> Any:
        return self._impl.shutdown_deployment(*args, **kwargs)


class NmpEmbedding(EmbeddingBackend, _Wrapper):
    def embed(self, *args: Any, **kwargs: Any) -> Any:
        return self._impl.get_embedding(*args, **kwargs)
