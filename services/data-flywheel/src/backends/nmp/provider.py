"""NMP backend provider — wires the NeMo Platform classes to the seam.

Each factory method returns a light wrapper (``src/backends/nmp/wrappers.py``)
that derives from the neutral base ABC and forwards NMP-specific methods to the
wrapped concrete class, so existing NMP call sites keep working unchanged.
"""

from __future__ import annotations

from typing import Any

from src.backends.base.provider import BackendProvider
from src.backends.nmp.customizer import Customizer
from src.backends.nmp.data_uploader import DataUploader
from src.backends.nmp.dms_client import DMSClient
from src.backends.nmp.embedding import Embedding
from src.backends.nmp.evaluator import Evaluator
from src.backends.nmp.llm_as_judge import validate_llm_judge
from src.backends.nmp.wrappers import (
    NmpDatastore,
    NmpDeploymentClient,
    NmpEmbedding,
    NmpEval,
    NmpTraining,
)
from src.job_types import JobType

#: NMP/NVIDIA default embedding model (an NMP-specific default, not a neutral one).
DEFAULT_EMBEDDING_MODEL = "nvidia/llama-3.2-nv-embedqa-1b-v2"


class NmpBackendProvider(BackendProvider):
    kind = "nmp"
    #: NMP runs both the LLM and embedding pipelines.
    supported_job_types = frozenset({JobType.LLM, JobType.EMBEDDING})

    def training(self) -> NmpTraining:
        return NmpTraining(Customizer())

    def evaluator(
        self,
        judge_model_config: Any | None = None,
        include_tools: bool = False,
        include_tool_choice: bool = False,
        include_nvext: bool = False,
    ) -> NmpEval:
        return NmpEval(
            Evaluator(
                judge_model_config=judge_model_config,
                include_tools=include_tools,
                include_tool_choice=include_tool_choice,
                include_nvext=include_nvext,
            )
        )

    def datastore(self, dataset_name: str) -> NmpDatastore:
        return NmpDatastore(DataUploader(dataset_name=dataset_name))

    def deployment_client(self, nim: Any) -> NmpDeploymentClient:
        return NmpDeploymentClient(DMSClient(nim=nim))

    def embedding(
        self,
        endpoint_url: str | None = None,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        api_key: str | None = None,
    ) -> NmpEmbedding:
        return NmpEmbedding(
            Embedding(endpoint_url=endpoint_url, model_name=model_name, api_key=api_key)
        )

    def validate_llm_judge(self) -> bool:
        # validate_llm_judge() returns None on success and exits the process on
        # failure, so reaching the return means the judge is available.
        validate_llm_judge()
        return True
