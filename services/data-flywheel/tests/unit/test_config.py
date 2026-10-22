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

import pytest
from pydantic import ValidationError

from src.config import (
    JOB_CONFIG_TYPES,
    AOIJobConfig,
    DataSplitConfig,
    DeftJobConfig,
    EmbeddingConfig,
    EmbeddingDataSplitConfig,
    EmbeddingJobConfig,
    ICLConfig,
    JobConfig,
    LLMJobConfig,
    LLMJudgeConfig,
    LoggingConfig,
    LoRAConfig,
    MLflowConfig,
    NIMConfig,
    NMPConfig,
    NmpSystemConfig,
    Settings,
    SimilarityConfig,
    SnapshotSelectionConfig,
    TrainingConfig,
    build_job_config,
    config_type_for,
)
from src.job_types import JobType

# One buildable body per REGISTERED config type, so the invariant test below can
# be parametrized over ``JOB_CONFIG_TYPES`` itself and therefore covers any type
# added later without being edited. Minimal on purpose: these exist to reach the
# builder, not to describe a realistic deployment.
_MINIMAL_CONFIG_BODIES: dict[str, dict] = {
    "llm_job": {
        "nims": [
            {
                "model_name": "meta/llama-3.2-1b-instruct",
                "context_length": 8192,
                "gpus": 1,
                "pvc_size": "25Gi",
                "tag": "1.0.0",
            }
        ],
        "icl_config": {},
        "training_config": {},
        "data_split_config": {},
    },
    "embedding_job": {
        "nims": [
            {
                "model_name": "nvidia/llama-nemotron-embed-1b-v2",
                "model_type": "embedding",
                "context_length": 8192,
                "gpus": 1,
                "pvc_size": "25Gi",
                "tag": "1.0.0",
            }
        ],
        "data_split_config": {},
        "training_config": {},
    },
    # Both DEFT keys get the SAME body, carrying ``use_case: "aoi"`` even for
    # ``its_job``: classification must follow the registry key, not the payload.
    "aoi_job": {
        "use_case": "aoi",
        "model_name": "changenet-aoi",
        "selection": {"filters": {"created_after": "1970-01-01T00:00:00Z"}},
    },
    "its_job": {
        "use_case": "aoi",
        "model_name": "changenet-aoi",
        "selection": {"filters": {"created_after": "1970-01-01T00:00:00Z"}},
    },
}


@pytest.fixture
def base_configs():
    """Base configurations for Settings creation."""
    return {
        "system_config": NmpSystemConfig(
            nmp_config=NMPConfig(
                datastore_base_url="http://test-datastore",
                nemo_base_url="http://test-nemo",
                nim_base_url="http://test-nim",
            ),
            llm_judge_config=LLMJudgeConfig(
                model_name="test-judge",
                context_length=8192,
                deployment_type="local",
            ),
            logging_config=LoggingConfig(),
            mlflow_config=MLflowConfig(),
        ),
        "job_configs": {
            "llm_job": LLMJobConfig(
                nims=[
                    NIMConfig(
                        model_name="test/model",
                        context_length=8192,
                        model_type="llm",
                    )
                ],
                data_split_config=DataSplitConfig(),
                icl_config=ICLConfig(),
                training_config=TrainingConfig(lora=LoRAConfig()),
            ),
        },
    }


class TestICLConfig:
    """Test ICLConfig validation."""

    def test_semantic_similarity_requires_embedding_config(self):
        """Test that semantic similarity selection requires embedding config."""
        with pytest.raises(ValidationError, match="similarity_config is required"):
            ICLConfig(example_selection="semantic_similarity", similarity_config=None)

    def test_semantic_similarity_with_embedding_config_succeeds(self):
        """Test that semantic similarity works with embedding config provided."""
        embedding_config = EmbeddingConfig(
            deployment_type="local",
            model_name="test-model",
            context_length=1024,
            gpus=1,
            pvc_size="10Gi",
            tag="latest",
        )

        similarity_config = SimilarityConfig(embedding_nim_config=embedding_config)

        config = ICLConfig(
            example_selection="semantic_similarity", similarity_config=similarity_config
        )

        assert config.example_selection == "semantic_similarity"
        assert config.similarity_config.embedding_nim_config is not None

    def test_uniform_distribution_no_embedding_config_required(self):
        """Test that uniform distribution doesn't require embedding config."""
        config = ICLConfig(example_selection="uniform_distribution")
        assert config.example_selection == "uniform_distribution"
        assert config.similarity_config is None

    @pytest.mark.parametrize(
        "max_examples,eval_size,should_pass",
        [
            (3, 10, True),  # Valid: max_examples < eval_size
            (5, 5, True),  # Valid: max_examples == eval_size
            (10, 5, False),  # Invalid: max_examples > eval_size
            (0, 5, True),  # Valid: zero max_examples
            (1, 0, False),  # Invalid: max_examples > zero eval_size
        ],
    )
    def test_validate_examples_limit(self, max_examples, eval_size, should_pass):
        """Test examples limit validation with different scenarios."""
        config = ICLConfig(max_examples=max_examples)

        if should_pass:
            config.validate_examples_limit(eval_size)  # Should not raise
        else:
            with pytest.raises(ValueError, match="cannot exceed"):
                config.validate_examples_limit(eval_size)


class TestLoadYamlData:
    """_load_yaml_data must fail clearly on empty / non-mapping YAML."""

    def test_empty_yaml_returns_empty_mapping(self, tmp_path):
        from src.config import Settings

        path = tmp_path / "empty.yaml"
        path.write_text("")
        assert Settings._load_yaml_data(path) == {}

    def test_non_mapping_yaml_raises_clear_error(self, tmp_path):
        from src.config import Settings

        path = tmp_path / "list.yaml"
        path.write_text("- just\n- a\n- list\n")
        with pytest.raises(ValueError, match="must be a mapping"):
            Settings._load_yaml_data(path)


class TestSettings:
    """Test Settings validation."""

    @pytest.mark.parametrize(
        "max_examples,eval_size,should_pass",
        [
            (3, 20, True),  # Default valid case
            (5, 5, True),  # Equal values
            (10, 5, False),  # Invalid case
            (100, 3, False),  # Large difference
        ],
    )
    def test_icl_examples_vs_eval_size(self, base_configs, max_examples, eval_size, should_pass):
        """Test ICL max_examples validation against eval_size."""
        from src.config import LLMJobConfig

        data_split_config = DataSplitConfig(eval_size=eval_size)
        icl_config = ICLConfig(max_examples=max_examples)

        if should_pass:
            # Create LLMJobConfig with the test configurations - should succeed
            llm_job = LLMJobConfig(
                nims=base_configs["job_configs"]["llm_job"].nims,
                data_split_config=data_split_config,
                icl_config=icl_config,
                training_config=base_configs["job_configs"]["llm_job"].training_config,
            )
            Settings(system_config=base_configs["system_config"], job_configs={"llm_job": llm_job})
        else:
            # Test that LLMJobConfig creation fails with validation error
            with pytest.raises(ValidationError, match="cannot exceed"):
                LLMJobConfig(
                    nims=base_configs["job_configs"]["llm_job"].nims,
                    data_split_config=data_split_config,
                    icl_config=icl_config,
                    training_config=base_configs["job_configs"]["llm_job"].training_config,
                )

    def test_default_config_values_are_valid(self, base_configs):
        """Test that default configuration values pass validation."""
        settings = Settings(**base_configs)

        assert settings.job_configs["llm_job"].icl_config.max_examples == 3
        assert settings.job_configs["llm_job"].data_split_config.eval_size == 100

    def test_settings_initialization_all_params(self):
        """Test Settings initialization with all parameters."""
        settings = Settings(
            system_config=NmpSystemConfig(
                nmp_config=NMPConfig(
                    datastore_base_url="http://test",
                    nemo_base_url="http://test",
                    nim_base_url="http://test",
                ),
                llm_judge_config=LLMJudgeConfig(
                    model_name="test", context_length=1024, deployment_type="local"
                ),
                logging_config=LoggingConfig(),
                mlflow_config=MLflowConfig(),
            ),
            job_configs={
                "llm_job": LLMJobConfig(
                    nims=[
                        NIMConfig(
                            model_name="test/model",
                            context_length=1024,
                            model_type="llm",
                        )
                    ],
                    data_split_config=DataSplitConfig(eval_size=50),
                    icl_config=ICLConfig(max_examples=5),
                    training_config=TrainingConfig(lora=LoRAConfig()),
                ),
            },
        )

        assert settings.job_configs["llm_job"].data_split_config.eval_size == 50
        assert settings.job_configs["llm_job"].icl_config.max_examples == 5


class TestConfigIntegration:
    """Test integration scenarios."""

    def test_semantic_similarity_with_examples_limit(self, base_configs):
        """Test semantic similarity config with examples limit validation."""
        from src.config import LLMJobConfig

        embedding_config = EmbeddingConfig(
            deployment_type="remote",
            model_name="test-embedding",
            url="http://test-url",
            api_key="test-key",
        )
        similarity_config = SimilarityConfig(embedding_nim_config=embedding_config)

        llm_job = LLMJobConfig(
            nims=base_configs["job_configs"]["llm_job"].nims,
            data_split_config=DataSplitConfig(eval_size=10),
            icl_config=ICLConfig(
                max_examples=5,
                example_selection="semantic_similarity",
                similarity_config=similarity_config,
            ),
            training_config=base_configs["job_configs"]["llm_job"].training_config,
        )

        settings = Settings(
            system_config=base_configs["system_config"],
            job_configs={"llm_job": llm_job},
        )

        assert settings.job_configs["llm_job"].icl_config.example_selection == "semantic_similarity"
        assert settings.job_configs["llm_job"].icl_config.max_examples == 5
        assert settings.job_configs["llm_job"].data_split_config.eval_size == 10

    def test_multiple_validation_errors(self, base_configs):
        """Test that multiple validation errors are handled properly."""
        # This should fail both semantic similarity and examples limit validation
        with pytest.raises(ValidationError):
            llm_job = LLMJobConfig(
                nims=base_configs["job_configs"]["llm_job"].nims,
                data_split_config=DataSplitConfig(eval_size=2),
                icl_config=ICLConfig(
                    max_examples=10,  # Too high
                    example_selection="semantic_similarity",
                    similarity_config=None,  # Missing
                ),
                training_config=base_configs["job_configs"]["llm_job"].training_config,
            )
            Settings(
                system_config=base_configs["system_config"],
                job_configs={"llm_job": llm_job},
            )


class TestSystemConfig:
    """Test SystemConfig validation."""

    def test_system_config_creation(self):
        """Test the NMP system config carries the shared + NMP-specific fields."""
        system_config = NmpSystemConfig(
            nmp_config=NMPConfig(
                datastore_base_url="http://test-datastore",
                nemo_base_url="http://test-nemo",
                nim_base_url="http://test-nim",
            ),
            llm_judge_config=LLMJudgeConfig(
                model_name="test-judge",
                context_length=8192,
                deployment_type="local",
            ),
            logging_config=LoggingConfig(),
            mlflow_config=MLflowConfig(),
        )

        assert system_config.nmp_config.datastore_base_url == "http://test-datastore"
        assert system_config.llm_judge_config.model_name == "test-judge"
        assert system_config.logging_config.level == "INFO"
        assert system_config.mlflow_config.enabled is not None


class TestLLMJobConfig:
    """Test LLMJobConfig validation."""

    def test_llm_job_config_creation(self):
        """Test LLMJobConfig creation with all required fields."""
        llm_job = LLMJobConfig(
            nims=[
                NIMConfig(
                    model_name="test/model",
                    context_length=8192,
                    model_type="llm",
                )
            ],
            data_split_config=DataSplitConfig(eval_size=20),
            icl_config=ICLConfig(max_examples=3),
            training_config=TrainingConfig(lora=LoRAConfig()),
        )

        assert len(llm_job.nims) == 1
        assert llm_job.nims[0].model_name == "test/model"
        assert llm_job.data_split_config.eval_size == 20
        assert llm_job.icl_config.max_examples == 3
        assert llm_job.training_config.lora.adapter_dim == 32

    def test_llm_job_config_empty_nims_validation(self):
        """Test LLMJobConfig validation with empty nims list."""
        with pytest.raises(ValidationError, match="At least one NIM must be configured"):
            LLMJobConfig(
                nims=[],
                data_split_config=DataSplitConfig(),
                icl_config=ICLConfig(),
                training_config=TrainingConfig(),
            )

    def test_llm_job_config_icl_validation(self):
        """Test LLMJobConfig ICL validation against data split."""
        with pytest.raises(ValidationError, match="cannot exceed"):
            LLMJobConfig(
                nims=[
                    NIMConfig(
                        model_name="test/model",
                        context_length=8192,
                        model_type="llm",
                    )
                ],
                data_split_config=DataSplitConfig(eval_size=5),
                icl_config=ICLConfig(max_examples=10),  # Too high
                training_config=TrainingConfig(),
            )


class TestEmbeddingJobConfig:
    """Test EmbeddingJobConfig validation."""

    def test_embedding_job_config_creation(self):
        """Test EmbeddingJobConfig creation with all required fields."""
        embedding_job = EmbeddingJobConfig(
            nims=[
                NIMConfig(
                    model_name="test/embedding-model",
                    context_length=1024,
                    model_type="embedding",
                )
            ],
            data_split_config=EmbeddingDataSplitConfig(),
            training_config=TrainingConfig(),
        )

        assert len(embedding_job.nims) == 1
        assert embedding_job.nims[0].model_name == "test/embedding-model"
        assert embedding_job.nims[0].model_type == "embedding"
        assert embedding_job.data_split_config.val_ratio == 0.1
        assert embedding_job.training_config.training_type == "sft"

    def test_embedding_job_config_empty_nims_validation(self):
        """Test EmbeddingJobConfig validation with empty nims list."""
        with pytest.raises(ValidationError, match="At least one NIM must be configured"):
            EmbeddingJobConfig(
                nims=[],
                data_split_config=EmbeddingDataSplitConfig(),
                training_config=TrainingConfig(),
            )

    def test_embedding_job_config_llm_model_validation(self):
        """Test EmbeddingJobConfig validation with LLM model (should fail)."""
        with pytest.raises(ValidationError, match="EmbeddingJobConfig requires embedding models"):
            EmbeddingJobConfig(
                nims=[
                    NIMConfig(
                        model_name="test/llm-model",
                        context_length=8192,
                        model_type="llm",  # Wrong type
                    )
                ],
                data_split_config=EmbeddingDataSplitConfig(),
                training_config=TrainingConfig(),
            )


class TestICLConfigAdditional:
    """Additional tests for ICL config edge cases."""

    def test_icl_missing_embedding_in_similarity_config(self):
        """Test that embedding_nim_config is required in similarity_config."""
        similarity_config = SimilarityConfig(embedding_nim_config=None)
        # Line 107 coverage: embedding_nim_config validation
        with pytest.raises(ValidationError, match="embedding_nim_config is required"):
            ICLConfig(example_selection="semantic_similarity", similarity_config=similarity_config)


class TestCustomizerTarget:
    """Test CustomizerTarget validation."""

    def test_hf_target_validation_missing_fields(self):
        """Test HuggingFace target validation with missing required fields."""
        from src.config import CustomizerTarget

        # Lines 146-173 coverage: HF target validation
        with pytest.raises(ValidationError, match="HuggingFace target requires"):
            CustomizerTarget(
                name="test/model",
                target_type="hf",
                # Missing: description, base_model, model_uri, hf_endpoint, num_parameters, precision
            )

    def test_hf_target_validation_invalid_uri(self):
        """Test HuggingFace target with invalid model_uri format."""
        from src.config import CustomizerTarget

        # Lines 168-171 coverage: model_uri validation
        with pytest.raises(ValidationError, match="model_uri must start with 'hf://'"):
            CustomizerTarget(
                name="test/model",
                target_type="hf",
                description="Test model",
                base_model="test/base",
                model_uri="wrong://model",  # Invalid format
                hf_endpoint="http://test",
                num_parameters=1000000,
                precision="bf16",
            )

    def test_hf_target_valid(self):
        """Test HuggingFace target with all required fields."""
        from src.config import CustomizerTarget

        target = CustomizerTarget(
            name="test/model",
            target_type="hf",
            description="Test model",
            base_model="test/base",
            model_uri="hf://model",
            hf_endpoint="http://test",
            num_parameters=1000000,
            precision="bf16",
        )
        assert target.target_type == "hf"
        assert target.model_uri.startswith("hf://")

    def test_nemo_target_no_validation(self):
        """Test nemo target doesn't require HF-specific fields."""
        from src.config import CustomizerTarget

        target = CustomizerTarget(
            name="test/model",
            target_type="nemo",
        )
        assert target.target_type == "nemo"


class TestCustomizerConfig:
    """Test CustomizerConfig."""

    def test_target_type_property_string(self):
        """Test target_type property with string target."""
        from src.config import CustomizerConfig

        # Lines 195-197 coverage: target_type property
        config = CustomizerConfig(gpus=1, target="meta/llama-3.2-1b-instruct@2.0")
        assert config.target_type == "nemo"

    def test_target_type_property_object(self):
        """Test target_type property with CustomizerTarget object."""
        from src.config import CustomizerConfig, CustomizerTarget

        target = CustomizerTarget(name="test/model", target_type="nemo")
        config = CustomizerConfig(gpus=1, target=target)
        assert config.target_type == "nemo"


class TestCustomEvaluation:
    """Test CustomEvaluation validation."""

    def test_custom_eval_empty_workload_id(self):
        """Test CustomEvaluation with empty workload_id."""
        from src.config import CustomEvaluation

        # Lines 219-220 coverage: workload_id validation
        with pytest.raises(ValidationError, match="workload_id cannot be None or empty"):
            CustomEvaluation(name="test_eval", workload_id="", client_id="test_client")

    def test_custom_eval_empty_client_id(self):
        """Test CustomEvaluation with empty client_id."""
        from src.config import CustomEvaluation

        # Lines 221-222 coverage: client_id validation
        with pytest.raises(ValidationError, match="client_id cannot be None or empty"):
            CustomEvaluation(name="test_eval", workload_id="test_workload", client_id="")


class TestEvaluationConfig:
    """Test EvaluationConfig validation."""

    def test_evaluation_config_duplicate_names(self):
        """Test EvaluationConfig with duplicate custom evaluation names."""
        from src.config import CustomEvaluation, EvaluationConfig

        # Line 243 coverage: unique names validation
        with pytest.raises(ValidationError, match="custom_evaluation names must be unique"):
            EvaluationConfig(
                custom_evaluations=[
                    CustomEvaluation(name="eval1", workload_id="w1", client_id="c1"),
                    CustomEvaluation(name="eval1", workload_id="w2", client_id="c2"),  # Duplicate
                ]
            )

    def test_evaluation_config_unique_names(self):
        """Test EvaluationConfig with unique names."""
        from src.config import CustomEvaluation, EvaluationConfig

        config = EvaluationConfig(
            custom_evaluations=[
                CustomEvaluation(name="eval1", workload_id="w1", client_id="c1"),
                CustomEvaluation(name="eval2", workload_id="w2", client_id="c2"),
            ]
        )
        assert len(config.custom_evaluations) == 2


class TestNIMConfig:
    """Test NIMConfig validation and methods."""

    def test_nim_customization_without_config(self):
        """Test NIM with customization enabled but no config."""
        # Line 269 coverage: customization validation
        with pytest.raises(ValidationError, match="customizer_configs is required"):
            NIMConfig(
                model_name="test/model",
                context_length=8192,
                customization_enabled=True,
                customizer_configs=None,
            )

    def test_nim_nmp_model_name(self):
        """Test NMP model name generation."""
        # Line 277 coverage: nmp_model_name method
        nim = NIMConfig(
            model_name="namespace/model@version",
            context_length=8192,
        )
        assert nim.nmp_model_name() == "namespace-model-version"

    def test_nim_generate_config_name_valid(self):
        """Test generate_config_name with valid base model."""
        # Lines 293-301 coverage: generate_config_name
        result = NIMConfig.generate_config_name("namespace/model")
        assert result == "model@v1.0.0+dfw"

    def test_nim_generate_config_name_none(self):
        """Test generate_config_name with None."""
        # Line 293-294 coverage: None validation
        with pytest.raises(ValueError, match="Invalid base model format"):
            NIMConfig.generate_config_name(None)

    def test_nim_generate_config_name_invalid_format(self):
        """Test generate_config_name with invalid format."""
        # Lines 297-298 coverage: Invalid format validation
        with pytest.raises(ValueError, match="Invalid base model format"):
            NIMConfig.generate_config_name("invalid_format")


class TestEmbeddingConfig:
    """Test EmbeddingConfig."""

    def test_embedding_remote_config(self):
        """Test remote embedding config creation."""
        # Line 507 coverage: remote_config classmethod
        from src.config import EmbeddingConfig

        config = EmbeddingConfig.remote_config(
            {"url": "http://test", "model_name": "test-model"}, "test-api-key"
        )
        assert config.deployment_type == "remote"
        assert config.is_remote

    def test_embedding_get_endpoint_url_remote(self):
        """Test get_endpoint_url for remote deployment."""
        # Line 531 coverage: remote endpoint
        config = EmbeddingConfig(
            deployment_type="remote",
            url="http://remote-endpoint",
            model_name="test-model",
            api_key="key",
        )
        assert config.get_endpoint_url() == "http://remote-endpoint"


class TestLLMJudgeConfig:
    """Test LLMJudgeConfig."""

    def test_llm_judge_remote_config(self):
        """Test remote LLM judge config creation."""
        # Line 564 coverage: remote_config classmethod
        config = LLMJudgeConfig.remote_config(
            {"url": "http://test", "model_name": "test-judge"}, "test-api-key"
        )
        assert config.deployment_type == "remote"
        assert config.is_remote

    def test_llm_judge_judge_model_config_remote(self):
        """Test judge_model_config for remote deployment."""
        # Lines 585-594 coverage: judge_model_config
        config = LLMJudgeConfig(
            deployment_type="remote", url="http://test", model_name="test-judge", api_key="key"
        )
        result = config.judge_model_config()
        assert "api_endpoint" in result
        assert result["api_endpoint"]["url"] == "http://test"


class TestJobConfigRegistry:
    """The central config_type -> schema registry + build/classify helpers."""

    def test_registry_covers_all_config_types(self):
        # Literal set: catches a REMOVAL, and states the current roster in one place.
        assert set(JOB_CONFIG_TYPES) == {"llm_job", "embedding_job", "aoi_job", "its_job"}
        # Derived set: catches an ADDITION. Four hand-written registries must agree
        # about which job types exist (JobType, JOB_CONFIG_TYPES, and the two
        # trigger Literals), and a literal-vs-literal assertion is add-BLIND — a
        # fifth JobType member would sail past it while /jobs accepted the new
        # type and /triggers 422'd it. src/job_types.py:44-48 says this drift must
        # not have to be found by hand a third time.
        assert set(JOB_CONFIG_TYPES) == {job_type.value for job_type in JobType}

    def test_both_deft_keys_resolve_to_the_one_deft_schema(self):
        """``aoi_job`` and ``its_job`` are ONE schema behind TWO registry keys.

        ITS needs no config field AOI lacks, so it is a distinct JOB TYPE riding
        the same contract. ``AOIJobConfig`` survives as a back-compat alias of the
        same object, so every existing importer and ``isinstance`` check is
        unchanged.
        """
        assert JOB_CONFIG_TYPES["aoi_job"] is DeftJobConfig
        assert JOB_CONFIG_TYPES["its_job"] is DeftJobConfig
        assert AOIJobConfig is DeftJobConfig

    def test_registry_is_read_only(self):
        # Frozen via MappingProxyType — accidental mutation must fail.
        with pytest.raises(TypeError):
            JOB_CONFIG_TYPES["x"] = LLMJobConfig  # type: ignore[index]

    @pytest.mark.parametrize("config_type", ["aoi_job", "its_job"])
    def test_build_job_config_happy_path_and_round_trip(self, config_type):
        cfg = build_job_config(
            config_type,
            {
                "use_case": "aoi",
                "model_name": "changenet-aoi",
                "selection": {
                    "mode": "query",
                    "filters": {"created_after": "1970-01-01T00:00:00Z"},
                },
            },
        )
        assert isinstance(cfg, DeftJobConfig)
        # NOTE the body says ``use_case: "aoi"`` for BOTH keys, deliberately.
        # job_type and use_case are disjoint axes: classification must follow the
        # registry key the config was BUILT under, never the payload.
        assert config_type_for(cfg) == config_type

    @pytest.mark.parametrize("config_type", sorted(JOB_CONFIG_TYPES))
    def test_config_type_for_round_trips_every_registered_type(self, config_type):
        """THE INVARIANT: ``config_type_for(build_job_config(t, data)) == t``.

        This is the test that would have caught the first-``isinstance``-match
        bug: with ``DeftJobConfig`` registered under two keys, a scan-based
        ``config_type_for`` classifies every ``its_job`` config as ``aoi_job``.
        """
        built = build_job_config(config_type, _MINIMAL_CONFIG_BODIES[config_type])
        assert config_type_for(built) == config_type

    @pytest.mark.parametrize("config_type", ["aoi_job", "its_job"])
    @pytest.mark.parametrize("use_case", ["aoi", "its", "anything_unregistered"])
    def test_classification_ignores_use_case_entirely(self, config_type, use_case):
        """``job_type`` and ``use_case`` are DISJOINT axes, in both directions.

        The crossed pairs are the load-bearing ones: an ``its_job`` config
        carrying ``use_case: "aoi"`` is still an ``its_job``, and an ``aoi_job``
        config carrying ``use_case: "its"`` is still an ``aoi_job``. The job type
        decides the SUBMISSION edge (pipeline, trigger branch, which YAML block
        is the deployment default); the use case decides the RUNTIME one
        (evaluator, object store, promotion policy). A classifier that sniffed
        ``use_case`` to tell the two DEFT keys apart would pass the uncrossed
        cases and silently mis-route these — hence the third, unrelated
        ``use_case`` value, which no job type is named after at all.
        """
        cfg = build_job_config(
            config_type, {**_MINIMAL_CONFIG_BODIES[config_type], "use_case": use_case}
        )
        assert cfg.use_case == use_case
        assert config_type_for(cfg) == config_type

    def test_the_stamp_never_reaches_the_serialized_config(self):
        """The stamp is a PrivateAttr, so persisted config_data is unchanged.

        ``ConfigurationDocument`` stores ``config_type`` as its own column beside
        ``model_dump()``; a stamp leaking into the dump would both duplicate and
        be able to CONTRADICT that column, and would show up in every stored
        Mongo document.
        """
        cfg = build_job_config("its_job", _MINIMAL_CONFIG_BODIES["its_job"])
        assert "_config_type" not in cfg.model_dump()
        assert "_config_type" not in cfg.model_dump(mode="json")
        assert "_config_type" not in cfg.__dict__
        assert "_config_type" not in type(cfg).model_fields

    def test_unstamped_deft_config_falls_back_to_the_legacy_answer(self):
        """A hand-built ``DeftJobConfig`` still classifies as ``aoi_job``.

        Nothing outside a registry door carries a stamp, so the ``isinstance``
        scan remains the fallback — and it answers exactly what every pre-ITS
        caller already got. Pinned so the back-compat guarantee is explicit
        rather than incidental to dict ordering.
        """
        assert config_type_for(DeftJobConfig.from_dict(_MINIMAL_CONFIG_BODIES["its_job"])) == (
            "aoi_job"
        )

    def test_aoi_snapshot_selection_requires_created_after(self):
        with pytest.raises(ValidationError, match="filters.created_after"):
            SnapshotSelectionConfig(mode="query")

    def test_build_job_config_unknown_type(self):
        with pytest.raises(ValueError, match="Unsupported configuration type"):
            build_job_config("not_a_job_type", {})

    def test_build_job_config_unknown_field(self):
        with pytest.raises(ValueError, match="unexpected field"):
            build_job_config(
                "aoi_job",
                {"use_case": "aoi", "model_name": "m", "not_a_real_field": 1},
            )

    def test_config_type_for_unregistered_raises(self):
        class Orphan(JobConfig):
            pass

        with pytest.raises(TypeError, match="Cannot classify"):
            config_type_for(Orphan())
