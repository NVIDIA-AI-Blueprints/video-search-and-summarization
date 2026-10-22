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

"""Unit tests for MLflow integration module."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.api.models import WorkloadClassification
from src.config import MLflowConfig
from src.lib.integration.mlflow_client import MLflowClient


class TestMLflowClient:
    """Test cases for MLflowClient class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mlflow_config = MLflowConfig(enabled=True)
        self.client = MLflowClient(self.mlflow_config)

        # Minimal realistic fixture based on actual results.json structure
        self.results_fixture = {
            "custom-tool-calling": [
                {
                    "item": {
                        "request": {
                            "model": "meta/llama-3.1-70b-instruct",
                            "messages": [
                                {"role": "system", "content": "system prompt"},
                                {"role": "user", "content": "user prompt"},
                            ],
                            "tools": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "structured_rag",
                                        "parameters": {
                                            "properties": {
                                                "query": {"type": "string"},
                                                "user_id": {"type": "string"},
                                            },
                                            "required": ["query", "user_id"],
                                            "type": "object",
                                        },
                                    },
                                },
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "ProductValidation",
                                        "parameters": {
                                            "properties": {"message": {"type": "string"}},
                                            "required": ["message"],
                                            "type": "object",
                                        },
                                    },
                                },
                            ],
                        },
                        "response": {
                            "choices": [
                                {
                                    "message": {
                                        "role": "assistant",
                                        "content": "",
                                        "tool_calls": [
                                            {
                                                "type": "function",
                                                "function": {
                                                    "name": "ProductValidation",
                                                    "arguments": {
                                                        "message": "What is the order status?"
                                                    },
                                                },
                                            }
                                        ],
                                    }
                                }
                            ]
                        },
                        "workload_id": "aiva_1",
                        "client_id": "dev",
                        "timestamp": 1746138417,
                    },
                    "sample": {
                        "output_text": "tool_call_output",
                        "response": {
                            "id": "chat-1",
                            "choices": [
                                {
                                    "finish_reason": "stop",
                                    "index": 0,
                                    "logprobs": None,
                                    "message": {
                                        "content": "assistant response",
                                        "refusal": None,
                                        "role": "assistant",
                                        "audio": None,
                                        "function_call": None,
                                        "tool_calls": [],
                                    },
                                    "stop_reason": 128008,
                                }
                            ],
                            "created": 1751986339,
                            "model": "meta/llama-3.2-1b-instruct",
                            "object": "chat.completion",
                            "service_tier": None,
                            "system_fingerprint": None,
                            "usage": {
                                "completion_tokens": 27,
                                "prompt_tokens": 1285,
                                "total_tokens": 1312,
                                "completion_tokens_details": None,
                                "prompt_tokens_details": None,
                            },
                            "prompt_logprobs": None,
                        },
                    },
                    "metrics": {
                        "tool-calling-accuracy": {
                            "scores": {
                                "function_name_accuracy": {"value": 0.0, "stats": None},
                                "function_name_and_args_accuracy": {"value": 0.0, "stats": None},
                            }
                        },
                        "correctness": {"scores": {"rating": {"value": 0.0, "stats": None}}},
                    },
                    "requests": [
                        {
                            "request": {
                                "model": "meta/llama-3.2-1b-instruct",
                                "messages": [
                                    {"content": "system prompt", "role": "system"},
                                    {"content": "user prompt", "role": "user"},
                                ],
                                "tools": [
                                    {
                                        "function": {
                                            "name": "structured_rag",
                                            "parameters": {
                                                "properties": {
                                                    "query": {"type": "string"},
                                                    "user_id": {"type": "string"},
                                                },
                                                "required": ["query", "user_id"],
                                                "type": "object",
                                            },
                                        },
                                        "type": "function",
                                    },
                                    {
                                        "function": {
                                            "name": "ProductValidation",
                                            "parameters": {
                                                "properties": {"message": {"type": "string"}},
                                                "required": ["message"],
                                                "type": "object",
                                            },
                                        },
                                        "type": "function",
                                    },
                                ],
                                "tool_choice": "required",
                            },
                            "response": {
                                "id": "chat-1",
                                "choices": [
                                    {
                                        "finish_reason": "stop",
                                        "index": 0,
                                        "logprobs": None,
                                        "message": {
                                            "content": "assistant response",
                                            "role": "assistant",
                                        },
                                        "stop_reason": 128008,
                                    }
                                ],
                                "created": 1751986339,
                                "model": "meta/llama-3.2-1b-instruct",
                                "object": "chat.completion",
                                "usage": {
                                    "completion_tokens": 27,
                                    "prompt_tokens": 1285,
                                    "total_tokens": 1312,
                                },
                                "prompt_logprobs": None,
                            },
                        }
                    ],
                }
            ]
        }

    def test_extract_metrics(self):
        """Test extracting metrics from evaluation item."""
        # Use the first item from our realistic fixture
        item = self.results_fixture["custom-tool-calling"][0]

        metrics = self.client._extract_metrics(item, WorkloadClassification.TOOL_CALLING)

        assert metrics["function_name_accuracy"] == 0.0
        assert metrics["function_name_and_args_accuracy"] == 0.0
        assert metrics["correctness_rating"] == 0.0
        assert metrics["total_tokens"] == 1312
        assert metrics["prompt_tokens"] == 1285
        assert metrics["completion_tokens"] == 27

    def test_extract_metadata(self):
        """Test extracting metadata from evaluation item."""
        # Use the first item from our realistic fixture
        item = self.results_fixture["custom-tool-calling"][0]

        metadata = self.client._extract_metadata(item)

        assert metadata["model"] == "meta/llama-3.2-1b-instruct"
        assert metadata["workload_id"] == "aiva_1"
        assert metadata["client_id"] == "dev"
        assert metadata["timestamp"] == 1746138417

    def test_load_results(self):
        """Test loading results from JSON file."""
        # Create temporary JSON file with realistic data
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(self.results_fixture, f)
            temp_file = Path(f.name)

        try:
            results = self.client._load_results(temp_file)
            assert results == self.results_fixture
            assert "custom-tool-calling" in results
            assert len(results["custom-tool-calling"]) == 1
        finally:
            temp_file.unlink()

    def test_load_results_file_not_found(self):
        """Test loading results from non-existent file."""
        with pytest.raises(FileNotFoundError):
            self.client._load_results(Path("/non/existent/file.json"))

    def test_extract_metrics_with_missing_values(self):
        """Test extracting metrics when some values are missing."""
        # Create an item with missing metrics
        item_with_missing = {
            "metrics": {
                "tool-calling-accuracy": {
                    "scores": {
                        "function_name_accuracy": {"value": 0.95},
                        # Missing function_name_and_args_accuracy
                    }
                },
                "correctness": {
                    "scores": {
                        "rating": {"value": 0.92},
                    }
                },
            },
            "sample": {
                "response": {
                    "usage": {
                        "total_tokens": 1500,
                        # Missing prompt_tokens and completion_tokens
                    }
                }
            },
        }

        metrics = self.client._extract_metrics(
            item_with_missing, WorkloadClassification.TOOL_CALLING
        )

        assert metrics["function_name_accuracy"] == 0.95
        assert metrics["function_name_and_args_accuracy"] is None
        assert metrics["correctness_rating"] == 0.92
        assert metrics["total_tokens"] == 1500
        assert metrics["prompt_tokens"] is None
        assert metrics["completion_tokens"] is None

    def test_extract_metrics_generic_workload(self):
        """Test extracting metrics for generic workload (llm-as-judge)."""
        # Create an item with llm-as-judge metrics
        item_generic = {
            "metrics": {
                "llm-judge": {
                    "scores": {
                        "similarity": {"value": 8.5},
                    }
                },
            },
            "sample": {
                "response": {
                    "usage": {
                        "total_tokens": 1200,
                        "prompt_tokens": 1000,
                        "completion_tokens": 200,
                    }
                }
            },
        }

        metrics = self.client._extract_metrics(item_generic, WorkloadClassification.GENERIC)

        assert metrics["similarity"] == 8.5
        assert metrics["total_tokens"] == 1200
        assert metrics["prompt_tokens"] == 1000
        assert metrics["completion_tokens"] == 200

    def test_extract_metadata_with_missing_values(self):
        """Test extracting metadata when some values are missing."""
        # Create an item with missing metadata
        item_with_missing = {
            "sample": {
                "response": {
                    # Missing model
                }
            },
            "item": {
                "workload_id": "workload_001",
                # Missing client_id and timestamp
            },
        }

        metadata = self.client._extract_metadata(item_with_missing)

        assert metadata["model"] is None
        assert metadata["workload_id"] == "workload_001"
        assert metadata["client_id"] is None
        assert metadata["timestamp"] is None

    @patch("src.lib.integration.mlflow_client.requests.get")
    def test_download_and_process_eval_success(self, mock_get):
        """Test successful download and processing of evaluation."""
        # Mock successful response
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.content = b"fake_zip_content"
        mock_get.return_value = mock_response

        # Mock zipfile operations
        with patch("src.lib.integration.mlflow_client.zipfile.ZipFile") as mock_zip:
            mock_zip.return_value.__enter__.return_value.namelist.return_value = ["file1", "file2"]

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                # Create a mock results.json file with realistic data
                results_file = temp_path / "results.json"
                results_file.write_text(json.dumps(self.results_fixture))

                # Mock the glob method by patching the Path class method
                with patch("pathlib.Path.glob") as mock_glob:
                    # Make glob return our results file
                    mock_glob.return_value = iter([results_file])

                    result = self.client.download_and_process_eval(
                        eval_id="test-eval",
                        nmp_eval_uri="http://test-uri",
                        save_dir=temp_path,
                        model="test/model",
                        eval_type="base-eval",
                    )

                    assert result is not None
                    assert result.name == "test_model_base-eval.json"

    @patch("src.lib.integration.mlflow_client.requests.get")
    def test_download_and_process_eval_failure(self, mock_get):
        """Test failed download of evaluation."""
        # Mock failed response
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 404
        mock_response.text = "Not found"
        mock_get.return_value = mock_response

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            result = self.client.download_and_process_eval(
                eval_id="test-eval",
                nmp_eval_uri="http://test-uri",
                save_dir=temp_path,
                model="test/model",
                eval_type="base-eval",
            )

            assert result is None

    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_upload_result_disabled(self, mock_mlflow):
        """Test MLflow upload when disabled."""
        mlflow_config = MLflowConfig(enabled=False)
        client = MLflowClient(mlflow_config)

        with tempfile.NamedTemporaryFile(suffix=".json") as f:
            temp_file = Path(f.name)

            result = client.upload_result(
                results_path=temp_file,
                flywheel_run_id="test-run",
                model_name="test/model",
                eval_type="base-eval",
            )

            assert result is None
            mock_mlflow.set_tracking_uri.assert_not_called()

    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_upload_result_enabled(self, mock_mlflow):
        """Test MLflow upload when enabled."""
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        # Mock MLflow operations
        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "exp-123"
        mock_mlflow.get_experiment_by_name.return_value = mock_experiment

        # Mock tracking URI
        mock_mlflow.get_tracking_uri.return_value = "http://mlflow:5000"

        # Create temporary results file with realistic data
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(self.results_fixture, f)
            temp_file = Path(f.name)

        try:
            result = client.upload_result(
                results_path=temp_file,
                flywheel_run_id="test-run",
                model_name="test/model",
                eval_type="base-eval",
                workload_type=WorkloadClassification.TOOL_CALLING,
            )

            assert result == "http://mlflow:5000/#/experiments/exp-123"
            mock_mlflow.set_tracking_uri.assert_called_once_with(mlflow_config.tracking_uri)
            mock_mlflow.log_param.assert_called()
            mock_mlflow.log_metrics.assert_called()

        finally:
            temp_file.unlink()

    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_cleanup_experiment_disabled(self, mock_mlflow):
        """Test MLflow cleanup when disabled."""
        mlflow_config = MLflowConfig(enabled=False)
        client = MLflowClient(mlflow_config)

        result = client.cleanup_experiment("test-experiment")

        assert result is True
        mock_mlflow.set_tracking_uri.assert_not_called()

    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_cleanup_experiment_enabled(self, mock_mlflow):
        """Test MLflow cleanup when enabled."""
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        # Mock experiment
        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "exp-123"
        mock_mlflow.get_experiment_by_name.return_value = mock_experiment

        result = client.cleanup_experiment("test-experiment")

        assert result is True
        mock_mlflow.set_tracking_uri.assert_called_once_with(mlflow_config.tracking_uri)
        mock_mlflow.delete_experiment.assert_called_once_with("exp-123")

    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_cleanup_experiment_not_found(self, mock_mlflow):
        """Test MLflow cleanup when experiment not found."""
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        # Mock experiment not found
        mock_mlflow.get_experiment_by_name.return_value = None

        result = client.cleanup_experiment("test-experiment")

        assert result is True
        mock_mlflow.delete_experiment.assert_not_called()

    @patch.object(MLflowClient, "upload_result")
    @patch.object(MLflowClient, "download_and_process_eval")
    def test_upload_evaluation_results_success(self, mock_download, mock_upload):
        """Test the full upload_evaluation_results workflow (success)."""
        mock_download.return_value = Path("/tmp/fake_results.json")
        mock_upload.return_value = "http://mlflow:5000/#/experiments/exp-123"

        result = self.client.upload_evaluation_results(
            job_id="job-1",
            nmp_eval_uri="http://nmp/uri",
            flywheel_run_id="fw-1",
            model_name="test/model",
            eval_type="base-eval",
        )

        assert result == "http://mlflow:5000/#/experiments/exp-123"
        mock_download.assert_called_once_with(
            eval_id="job-1",
            nmp_eval_uri="http://nmp/uri",
            save_dir=Path("./mlruns/temp_mlflow_results_job-1"),
            model="test/model",
            eval_type="base-eval",
        )
        mock_upload.assert_called_once_with(
            results_path=Path("/tmp/fake_results.json"),
            flywheel_run_id="fw-1",
            model_name="test/model",
            eval_type="base-eval",
            workload_type=WorkloadClassification.GENERIC,
        )

    @patch.object(MLflowClient, "upload_result")
    @patch.object(MLflowClient, "download_and_process_eval")
    def test_upload_evaluation_results_download_fail(self, mock_download, mock_upload):
        """Test upload_evaluation_results returns None if download fails."""
        mock_download.return_value = None
        result = self.client.upload_evaluation_results(
            job_id="job-1",
            nmp_eval_uri="http://nmp/uri",
            flywheel_run_id="fw-1",
            model_name="test/model",
            eval_type="base-eval",
        )
        assert result is None
        mock_download.assert_called_once()
        mock_upload.assert_not_called()

    @patch("src.lib.integration.mlflow_client.shutil.rmtree")
    @patch.object(MLflowClient, "upload_result")
    @patch.object(MLflowClient, "download_and_process_eval")
    def test_upload_evaluation_results_cleanup_on_success(
        self, mock_download, mock_upload, mock_rmtree
    ):
        """Test that temporary directory is cleaned up on successful upload."""
        mock_download.return_value = Path("/tmp/fake_results.json")
        mock_upload.return_value = "http://mlflow:5000/#/experiments/exp-123"

        result = self.client.upload_evaluation_results(
            job_id="job-1",
            nmp_eval_uri="http://nmp/uri",
            flywheel_run_id="fw-1",
            model_name="test/model",
            eval_type="base-eval",
        )

        assert result == "http://mlflow:5000/#/experiments/exp-123"
        # Verify cleanup was called
        mock_rmtree.assert_called_once_with(Path("./mlruns/temp_mlflow_results_job-1"))

    @patch("src.lib.integration.mlflow_client.shutil.rmtree")
    @patch.object(MLflowClient, "upload_result")
    @patch.object(MLflowClient, "download_and_process_eval")
    def test_upload_evaluation_results_cleanup_on_exception(
        self, mock_download, mock_upload, mock_rmtree
    ):
        """Test that temporary directory is cleaned up even when an exception occurs."""
        mock_download.side_effect = Exception("Download failed")

        result = self.client.upload_evaluation_results(
            job_id="job-1",
            nmp_eval_uri="http://nmp/uri",
            flywheel_run_id="fw-1",
            model_name="test/model",
            eval_type="base-eval",
        )

        # Method should return None when exception occurs
        assert result is None
        # Verify cleanup was called even though an exception occurred
        mock_rmtree.assert_called_once_with(Path("./mlruns/temp_mlflow_results_job-1"))

    @patch("src.lib.integration.mlflow_client.shutil.rmtree")
    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_cleanup_experiment_with_artifacts(self, mock_mlflow, mock_rmtree):
        """Test MLflow cleanup when enabled, including artifact cleanup."""
        mlflow_config = MLflowConfig(enabled=True, artifact_location="./mlruns")
        client = MLflowClient(mlflow_config)

        # Mock experiment
        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "exp-123"
        mock_mlflow.get_experiment_by_name.return_value = mock_experiment

        # Mock Path.exists to return True for artifact directory
        with patch("pathlib.Path.exists", return_value=True):
            result = client.cleanup_experiment("test-experiment")

        assert result is True
        mock_mlflow.set_tracking_uri.assert_called_once_with(mlflow_config.tracking_uri)
        mock_mlflow.delete_experiment.assert_called_once_with("exp-123")
        # Verify artifact cleanup was called
        mock_rmtree.assert_called_once_with(Path("./mlruns/exp-123"))

    @patch("src.lib.integration.mlflow_client.shutil.rmtree")
    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_cleanup_experiment_artifacts_not_found(self, mock_mlflow, mock_rmtree):
        """Test MLflow cleanup when artifacts directory doesn't exist."""
        mlflow_config = MLflowConfig(enabled=True, artifact_location="./mlruns")
        client = MLflowClient(mlflow_config)

        # Mock experiment
        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "exp-123"
        mock_mlflow.get_experiment_by_name.return_value = mock_experiment

        # Mock Path.exists to return False for artifact directory
        with patch("pathlib.Path.exists", return_value=False):
            result = client.cleanup_experiment("test-experiment")

        assert result is True
        mock_mlflow.delete_experiment.assert_called_once_with("exp-123")
        # Verify artifact cleanup was NOT called since directory doesn't exist
        mock_rmtree.assert_not_called()

    @patch("src.lib.integration.mlflow_client.shutil.rmtree")
    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_cleanup_experiment_artifact_cleanup_fails(self, mock_mlflow, mock_rmtree):
        """Test MLflow cleanup continues even if artifact cleanup fails."""
        mlflow_config = MLflowConfig(enabled=True, artifact_location="./mlruns")
        client = MLflowClient(mlflow_config)

        # Mock experiment
        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "exp-123"
        mock_mlflow.get_experiment_by_name.return_value = mock_experiment

        # Mock artifact cleanup to fail
        mock_rmtree.side_effect = Exception("Artifact cleanup failed")

        # Mock Path.exists to return True for artifact directory
        with patch("pathlib.Path.exists", return_value=True):
            result = client.cleanup_experiment("test-experiment")

        assert result is True
        mock_mlflow.delete_experiment.assert_called_once_with("exp-123")
        # Verify artifact cleanup was attempted
        mock_rmtree.assert_called_once_with(Path("./mlruns/exp-123"))


class TestMLflowClientDPOWorkload:
    """Test DPO workload-specific functionality."""

    def test_extract_metrics_dpo_workload(self):
        """Test extracting metrics for DPO workload."""
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        item_dpo = {
            "metrics": {
                "dpo-preference-alignment": {
                    "scores": {
                        "preference_score": {"value": 0.85},
                    }
                },
            },
            "sample": {
                "response": {
                    "usage": {
                        "total_tokens": 1500,
                        "prompt_tokens": 1200,
                        "completion_tokens": 300,
                    }
                }
            },
        }

        metrics = client._extract_metrics(item_dpo, WorkloadClassification.DPO)

        assert metrics["preference_score"] == 0.85
        assert metrics["total_tokens"] == 1500
        assert metrics["prompt_tokens"] == 1200
        assert metrics["completion_tokens"] == 300

    def test_extract_metrics_dpo_with_missing_values(self):
        """Test extracting DPO metrics with missing values."""
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        item_dpo = {
            "metrics": {
                "dpo-preference-alignment": {
                    "scores": {}  # Empty scores
                },
            },
            "sample": {
                "response": {
                    "usage": {
                        "total_tokens": 1000,
                    }
                }
            },
        }

        metrics = client._extract_metrics(item_dpo, WorkloadClassification.DPO)

        assert metrics["preference_score"] is None
        assert metrics["total_tokens"] == 1000

    def test_extract_metrics_exception_handling(self):
        """Test exception handling in _extract_metrics."""
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        # Malformed item that will cause exception
        malformed_item = None

        metrics = client._extract_metrics(malformed_item, WorkloadClassification.GENERIC)

        # Should return empty dict on exception
        assert metrics == {}

    def test_extract_metadata_exception_handling(self):
        """Test exception handling in _extract_metadata."""
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        # Malformed item that will cause exception
        malformed_item = None

        metadata = client._extract_metadata(malformed_item)

        # Should return empty dict on exception
        assert metadata == {}


class TestMLflowClientDownloadErrors:
    """Test error handling in download_and_process_eval."""

    @patch("src.lib.integration.mlflow_client.requests.get")
    @patch("src.lib.integration.mlflow_client.zipfile.ZipFile")
    def test_download_results_json_not_found(self, mock_zipfile, mock_get):
        """Test when results.json is not found in extracted files."""
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        # Mock successful download
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.content = b"fake_zip_content"
        mock_get.return_value = mock_response

        # Mock zip extraction but no results.json
        mock_zip_instance = MagicMock()
        mock_zip_instance.namelist.return_value = ["other_file.txt", "data.csv"]
        mock_zipfile.return_value.__enter__.return_value = mock_zip_instance

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Mock glob to return empty (no results.json found)
            with patch("pathlib.Path.glob") as mock_glob:
                mock_glob.return_value = iter([])  # No files found

                result = client.download_and_process_eval(
                    eval_id="test-eval",
                    nmp_eval_uri="http://test-uri",
                    save_dir=temp_path,
                    model="test/model",
                    eval_type="base-eval",
                )

                assert result is None

    @patch("src.lib.integration.mlflow_client.requests.get")
    def test_download_exception_handling(self, mock_get):
        """Test exception handling during download."""
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        # Mock exception during download
        mock_get.side_effect = Exception("Network error")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            result = client.download_and_process_eval(
                eval_id="test-eval",
                nmp_eval_uri="http://test-uri",
                save_dir=temp_path,
                model="test/model",
                eval_type="base-eval",
            )

            assert result is None


class TestMLflowClientEmbeddingUpload:
    """Test upload_embedding_results functionality."""

    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_upload_embedding_results_success(self, mock_mlflow):
        """Test successful upload of embedding evaluation results."""
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        # Mock MLflow operations
        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "exp-456"
        mock_mlflow.get_experiment_by_name.return_value = mock_experiment
        mock_mlflow.get_tracking_uri.return_value = "http://mlflow:5000"

        scores = {
            "ndcg@10": 0.85,
            "recall@10": 0.92,
            "map@10": 0.78,
        }

        result = client.upload_embedding_results(
            flywheel_run_id="fw-123",
            model_name="embedding/model",
            eval_type="retrieval-eval",
            scores=scores,
        )

        assert result == "http://mlflow:5000/#/experiments/exp-456"
        mock_mlflow.log_param.assert_called()
        mock_mlflow.log_metrics.assert_called_once_with(scores)

    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_upload_embedding_results_create_experiment(self, mock_mlflow):
        """Test upload_embedding_results creates new experiment if not exists."""
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        # Mock no existing experiment
        mock_mlflow.get_experiment_by_name.return_value = None
        mock_mlflow.create_experiment.return_value = "exp-new"
        mock_mlflow.get_tracking_uri.return_value = "http://mlflow:5000/"  # With trailing slash

        scores = {"ndcg@10": 0.85}

        result = client.upload_embedding_results(
            flywheel_run_id="fw-123",
            model_name="embedding/model",
            eval_type="retrieval-eval",
            scores=scores,
        )

        assert result == "http://mlflow:5000/#/experiments/exp-new"
        mock_mlflow.create_experiment.assert_called_once()

    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_upload_embedding_results_disabled(self, mock_mlflow):
        """Test upload_embedding_results when MLflow is disabled."""
        mlflow_config = MLflowConfig(enabled=False)
        client = MLflowClient(mlflow_config)

        result = client.upload_embedding_results(
            flywheel_run_id="fw-123",
            model_name="embedding/model",
            eval_type="retrieval-eval",
            scores={"ndcg@10": 0.85},
        )

        assert result is None
        mock_mlflow.get_experiment_by_name.assert_not_called()

    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_upload_embedding_results_with_none_values(self, mock_mlflow):
        """Test upload_embedding_results filters out None values."""
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "exp-456"
        mock_mlflow.get_experiment_by_name.return_value = mock_experiment
        mock_mlflow.get_tracking_uri.return_value = "http://mlflow:5000"

        scores = {
            "ndcg@10": 0.85,
            "recall@10": None,  # Should be filtered out
            "map@10": 0.78,
        }

        result = client.upload_embedding_results(
            flywheel_run_id="fw-123",
            model_name="embedding/model",
            eval_type="retrieval-eval",
            scores=scores,
        )

        assert result == "http://mlflow:5000/#/experiments/exp-456"
        # Verify only non-None values were logged
        call_args = mock_mlflow.log_metrics.call_args[0][0]
        assert "ndcg@10" in call_args
        assert "map@10" in call_args
        assert "recall@10" not in call_args

    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_upload_embedding_results_exception(self, mock_mlflow):
        """Test upload_embedding_results handles exceptions gracefully."""
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        # Mock exception
        mock_mlflow.get_experiment_by_name.side_effect = Exception("MLflow error")

        result = client.upload_embedding_results(
            flywheel_run_id="fw-123",
            model_name="embedding/model",
            eval_type="retrieval-eval",
            scores={"ndcg@10": 0.85},
        )

        assert result is None


class TestMLflowClientAOIPromotion:
    """Tests for the AOI promotion registry/alias/tag methods.

    These patch only ``mlflow.MlflowClient`` (not the whole ``mlflow`` module) so that
    ``mlflow.exceptions.MlflowException`` stays a real class for the ``except`` clauses.
    """

    def _client(self, enabled: bool) -> MLflowClient:
        return MLflowClient(MLflowConfig(enabled=enabled))

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_register_candidate_full_flow(self, mock_client_cls):
        """register_candidate ensures the model, creates a version, sets @candidate + tags,
        and records the Mongo serving integer on the version_label tag (best-effort — F3)."""
        client = self._client(enabled=True)

        registry = MagicMock()
        mock_client_cls.return_value = registry
        # MLflow assigns its own integer-string version number (internal only).
        created = MagicMock()
        created.version = "7"
        registry.create_model_version.return_value = created

        result = client.register_candidate(
            model_name="aoi-defect",
            deft_run_id="run-231b-003",
            serving_version="2",
            artifact_uri="s3://bucket/candidate",
            metrics={"mAP50": 0.91, "far": 0.0007},
            tags={
                "promotion.state": "candidate",
                "snapshot_id": "snap-9",
            },
        )

        # Best-effort: a truthy bool signals the tracking trail was recorded.
        assert result is True

        registry.create_registered_model.assert_called_once_with("aoi-defect")

        # Version created against the artifact URI with merged promotion + metric tags.
        _, kwargs = registry.create_model_version.call_args
        assert kwargs["name"] == "aoi-defect"
        assert kwargs["source"] == "s3://bucket/candidate"
        version_tags = kwargs["tags"]
        assert version_tags["promotion.state"] == "candidate"
        assert version_tags["snapshot_id"] == "snap-9"
        # The DEFT run id rides its own queryable lineage tag (NOT the version number).
        assert version_tags["promotion.deft_run_id"] == "run-231b-003"
        # The serving/Triton version label is the Mongo integer (so the MLflow
        # UI shows 1, 2, 3 — NOT MLflow's own internal version number "7").
        assert version_tags["promotion.version_label"] == "2"
        # Metrics stored as version tags.
        assert version_tags["metric.mAP50"] == "0.91"
        assert version_tags["metric.far"] == "0.0007"

        # Alias bound to the MLflow-created version number.
        registry.set_registered_model_alias.assert_called_once_with("aoi-defect", "candidate", "7")

    @patch("src.lib.integration.mlflow_client.mlflow.set_experiment")
    @patch("src.lib.integration.mlflow_client.mlflow.log_dict")
    @patch("src.lib.integration.mlflow_client.mlflow.start_run")
    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_register_candidate_falls_back_to_tracking_run_pointer(
        self, mock_client_cls, mock_start_run, mock_log_dict, mock_set_experiment
    ):
        """MLflow rejects file:// sources (local-path validation) — register_candidate
        falls back to a tracking-run pointer artifact; the TRUE URI rides
        the checkpoint_uri tag (which get_version_source prefers)."""
        from mlflow.exceptions import MlflowException
        from mlflow.protos.databricks_pb2 import INVALID_PARAMETER_VALUE

        client = self._client(enabled=True)
        registry = MagicMock()
        mock_client_cls.return_value = registry
        created = MagicMock()
        created.version = "8"
        registry.create_model_version.side_effect = [
            MlflowException("Invalid model version source", error_code=INVALID_PARAMETER_VALUE),
            created,
        ]
        run = MagicMock()
        run.info.run_id = "trk-run-1"
        run.info.artifact_uri = "mlflow-artifacts:/0/trk-run-1/artifacts"
        mock_start_run.return_value.__enter__.return_value = run

        result = client.register_candidate(
            model_name="aoi-defect",
            deft_run_id="run-uuid",
            serving_version="3",
            artifact_uri="file:///data/deft/run/weights.pt",
            metrics={},
            tags={},
        )

        # Fallback still records the tracking trail (best-effort).
        assert result is True
        assert registry.create_model_version.call_count == 2
        mock_log_dict.assert_called_once()  # the pointer document
        retry_kwargs = registry.create_model_version.call_args.kwargs
        assert retry_kwargs["source"] == "mlflow-artifacts:/0/trk-run-1/artifacts"
        assert retry_kwargs["run_id"] == "trk-run-1"
        assert retry_kwargs["tags"]["checkpoint_uri"] == "file:///data/deft/run/weights.pt"
        assert retry_kwargs["tags"]["promotion.deft_run_id"] == "run-uuid"
        assert retry_kwargs["tags"]["promotion.version_label"] == "3"
        registry.set_registered_model_alias.assert_called_once_with("aoi-defect", "candidate", "8")

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_register_candidate_returns_false_on_failure(self, mock_client_cls):
        client = self._client(enabled=True)
        registry = MagicMock()
        mock_client_cls.return_value = registry
        registry.create_model_version.side_effect = RuntimeError("registry down")
        assert (
            client.register_candidate(
                model_name="m",
                deft_run_id="run-1",
                serving_version="1",
                artifact_uri="s3://a",
                metrics={},
                tags={},
            )
            is False
        )

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_get_version_source_prefers_checkpoint_uri_tag(self, mock_client_cls):
        # `source` may carry the scheme-prefixed value mangled to pass MLflow
        # validation — the TRUE locator is the checkpoint_uri tag.
        client = self._client(enabled=True)
        registry = MagicMock()
        mock_client_cls.return_value = registry
        mv = MagicMock()
        mv.source = "mlflow-artifacts:/0/trk-run-1/artifacts"
        mv.tags = {"checkpoint_uri": "file:///data/deft/run/weights.pt"}
        registry.get_model_version.return_value = mv
        assert client.get_version_source("m", "8") == "file:///data/deft/run/weights.pt"

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_register_candidate_ignores_already_exists(self, mock_client_cls):
        """A RESOURCE_ALREADY_EXISTS error from create_registered_model is swallowed."""
        from mlflow.exceptions import MlflowException
        from mlflow.protos.databricks_pb2 import RESOURCE_ALREADY_EXISTS

        client = self._client(enabled=True)

        registry = MagicMock()
        mock_client_cls.return_value = registry
        registry.create_registered_model.side_effect = MlflowException(
            "exists", error_code=RESOURCE_ALREADY_EXISTS
        )

        created = MagicMock()
        created.version = "2"
        registry.create_model_version.return_value = created

        result = client.register_candidate(
            model_name="aoi-defect",
            deft_run_id="run-1",
            serving_version="4",
            artifact_uri="s3://bucket/candidate",
            metrics={},
            tags={"promotion.state": "candidate", "snapshot_id": "s"},
        )

        # Despite the already-exists error, the version + alias are still created.
        assert result is True
        registry.create_model_version.assert_called_once()
        registry.set_registered_model_alias.assert_called_once_with("aoi-defect", "candidate", "2")

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_register_candidate_other_mlflow_errors_fail_soft(self, mock_client_cls):
        """A non-already-exists error is caught by the outer guard (no crash, no record)."""
        from mlflow.exceptions import MlflowException
        from mlflow.protos.databricks_pb2 import INTERNAL_ERROR

        client = self._client(enabled=True)

        registry = MagicMock()
        mock_client_cls.return_value = registry
        registry.create_registered_model.side_effect = MlflowException(
            "nope", error_code=INTERNAL_ERROR
        )

        # Should not raise; a failed registration returns False (best-effort).
        result = client.register_candidate(
            model_name="aoi-defect",
            deft_run_id="run-1",
            serving_version="1",
            artifact_uri="s3://bucket/candidate",
            metrics={},
            tags={},
        )

        assert result is False
        registry.create_model_version.assert_not_called()

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_register_candidate_disabled_noop(self, mock_client_cls):
        """register_candidate no-ops (returns False) when MLflow is disabled."""
        client = self._client(enabled=False)
        result = client.register_candidate(
            model_name="m",
            deft_run_id="run-1",
            serving_version="1",
            artifact_uri="s3://x",
            metrics={"a": 1.0},
            tags={"promotion.state": "candidate"},
        )
        assert result is False  # nothing recorded (disabled), reported honestly
        mock_client_cls.assert_not_called()

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_set_alias_progression(self, mock_client_cls):
        """set_alias moves an alias to a version through the progression."""
        client = self._client(enabled=True)
        registry = MagicMock()
        mock_client_cls.return_value = registry

        for alias in ("shadow", "canary", "production"):
            client.set_alias("aoi-defect", alias, "7")

        assert registry.set_registered_model_alias.call_args_list == [
            (("aoi-defect", "shadow", "7"),),
            (("aoi-defect", "canary", "7"),),
            (("aoi-defect", "production", "7"),),
        ]

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_set_alias_disabled_noop(self, mock_client_cls):
        client = self._client(enabled=False)
        assert client.set_alias("m", "production", "3") is None
        mock_client_cls.assert_not_called()

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_set_alias_fails_soft(self, mock_client_cls):
        """A registry error during set_alias does not propagate."""
        client = self._client(enabled=True)
        registry = MagicMock()
        mock_client_cls.return_value = registry
        registry.set_registered_model_alias.side_effect = RuntimeError("boom")

        assert client.set_alias("m", "production", "3") is None

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_get_version_by_alias_returns_version(self, mock_client_cls):
        client = self._client(enabled=True)
        registry = MagicMock()
        mock_client_cls.return_value = registry
        bound = MagicMock()
        bound.version = "5"
        registry.get_model_version_by_alias.return_value = bound

        assert client.get_version_by_alias("aoi-defect", "production") == "5"
        registry.get_model_version_by_alias.assert_called_once_with("aoi-defect", "production")

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_get_version_by_alias_absent_returns_none(self, mock_client_cls):
        """When the alias is not bound, get_version_by_alias returns None."""
        from mlflow.exceptions import MlflowException

        client = self._client(enabled=True)
        registry = MagicMock()
        mock_client_cls.return_value = registry
        registry.get_model_version_by_alias.side_effect = MlflowException("not found")

        assert client.get_version_by_alias("aoi-defect", "canary") is None

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_get_version_by_alias_disabled_noop(self, mock_client_cls):
        client = self._client(enabled=False)
        assert client.get_version_by_alias("m", "candidate") is None
        mock_client_cls.assert_not_called()

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_get_serving_version_by_alias_returns_label_not_alias_number(self, mock_client_cls):
        """F3 (Faaran review): the SERVING integer is the promotion.version_label
        tag, NOT MLflow's own alias version number — they can diverge."""
        client = self._client(enabled=True)
        registry = MagicMock()
        mock_client_cls.return_value = registry
        bound = MagicMock()
        bound.version = "5"  # MLflow's own auto-assigned number
        bound.tags = {"promotion.version_label": "2"}  # the DFW/Mongo serving integer
        registry.get_model_version_by_alias.return_value = bound

        assert client.get_serving_version_by_alias("aoi-defect", "production") == "2"
        registry.get_model_version_by_alias.assert_called_once_with("aoi-defect", "production")

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_get_serving_version_by_alias_absent_tag_returns_none(self, mock_client_cls):
        """No promotion.version_label tag → None (a wrong baseline is worse than none)."""
        client = self._client(enabled=True)
        registry = MagicMock()
        mock_client_cls.return_value = registry
        bound = MagicMock()
        bound.version = "5"
        bound.tags = {}  # no serving-version tag
        registry.get_model_version_by_alias.return_value = bound

        assert client.get_serving_version_by_alias("aoi-defect", "production") is None

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_get_serving_version_by_alias_unbound_returns_none(self, mock_client_cls):
        from mlflow.exceptions import MlflowException

        client = self._client(enabled=True)
        registry = MagicMock()
        mock_client_cls.return_value = registry
        registry.get_model_version_by_alias.side_effect = MlflowException("not found")

        assert client.get_serving_version_by_alias("aoi-defect", "canary") is None

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_get_serving_version_by_alias_disabled_noop(self, mock_client_cls):
        client = self._client(enabled=False)
        assert client.get_serving_version_by_alias("m", "production") is None
        mock_client_cls.assert_not_called()

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_set_and_get_promotion_tags_round_trip(self, mock_client_cls):
        """set_promotion_tags writes each tag; get_promotion_tags reads them back."""
        client = self._client(enabled=True)
        registry = MagicMock()
        mock_client_cls.return_value = registry

        tags = {
            "promotion.state": "pending_approval",
            "promotion.pending_gate": "canary",
            "gate.canary.decision": "approved",
            "gate.canary.by": "alice",
        }
        client.set_promotion_tags("aoi-defect", "7", tags)

        # Every tag written via set_model_version_tag (values coerced to str).
        assert registry.set_model_version_tag.call_count == len(tags)
        written = {
            call.args[2]: call.args[3] for call in registry.set_model_version_tag.call_args_list
        }
        assert written == {k: str(v) for k, v in tags.items()}

        # Read-back round trip.
        mv = MagicMock()
        mv.tags = dict(tags)
        registry.get_model_version.return_value = mv
        assert client.get_promotion_tags("aoi-defect", "7") == tags
        registry.get_model_version.assert_called_once_with("aoi-defect", "7")

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_set_promotion_tags_coerces_values(self, mock_client_cls):
        """Non-string tag values are coerced to str."""
        client = self._client(enabled=True)
        registry = MagicMock()
        mock_client_cls.return_value = registry

        client.set_promotion_tags("m", "1", {"gate.full.eval": 0.98})
        registry.set_model_version_tag.assert_called_once_with("m", "1", "gate.full.eval", "0.98")

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_get_promotion_tags_empty_when_no_tags(self, mock_client_cls):
        client = self._client(enabled=True)
        registry = MagicMock()
        mock_client_cls.return_value = registry
        mv = MagicMock()
        mv.tags = None
        registry.get_model_version.return_value = mv

        assert client.get_promotion_tags("m", "1") == {}

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_get_promotion_tags_fails_soft(self, mock_client_cls):
        client = self._client(enabled=True)
        registry = MagicMock()
        mock_client_cls.return_value = registry
        registry.get_model_version.side_effect = RuntimeError("boom")

        assert client.get_promotion_tags("m", "1") == {}

    @patch("src.lib.integration.mlflow_client.mlflow.MlflowClient")
    def test_promotion_tags_disabled_noop(self, mock_client_cls):
        client = self._client(enabled=False)
        assert client.set_promotion_tags("m", "1", {"promotion.state": "x"}) is None
        assert client.get_promotion_tags("m", "1") == {}
        mock_client_cls.assert_not_called()


class TestMLflowClientUploadEvaluationResultsErrors:
    """Test error handling in upload_evaluation_results."""

    @patch("src.lib.integration.mlflow_client.shutil.rmtree")
    @patch.object(MLflowClient, "upload_result")
    @patch.object(MLflowClient, "download_and_process_eval")
    def test_upload_evaluation_results_upload_exception(
        self, mock_download, mock_upload, mock_rmtree
    ):
        """Test upload_evaluation_results handles upload exceptions."""
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        mock_download.return_value = Path("/tmp/fake_results.json")
        mock_upload.side_effect = Exception("Upload failed")

        result = client.upload_evaluation_results(
            job_id="job-1",
            nmp_eval_uri="http://nmp/uri",
            flywheel_run_id="fw-1",
            model_name="test/model",
            eval_type="base-eval",
        )

        # Should return None on exception
        assert result is None
        # Should still cleanup
        mock_rmtree.assert_called_once()


class TestMLflowClientAdditionalCoverage:
    """Additional tests to improve coverage."""

    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_upload_result_creates_new_experiment(self, mock_mlflow):
        """Test that upload_result creates a new experiment if it doesn't exist."""
        # Lines 211-214 coverage: create_experiment path
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        # Mock no existing experiment
        mock_mlflow.get_experiment_by_name.return_value = None
        mock_mlflow.create_experiment.return_value = "new-exp-id"

        # Create a temp results file
        with tempfile.TemporaryDirectory() as tmpdir:
            results_path = Path(tmpdir) / "results.json"
            results_path.write_text(json.dumps({"llm-as-judge": []}))

            client.upload_result(
                results_path=results_path,
                flywheel_run_id="fw-123",
                model_name="test/model",
                eval_type="base-eval",
                workload_type=WorkloadClassification.GENERIC,
            )

        # Should have created new experiment
        mock_mlflow.create_experiment.assert_called_once()

    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_upload_result_dpo_workload(self, mock_mlflow):
        """Test upload_result with DPO workload type."""
        # Lines 226-227 coverage: DPO task_key
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "exp-id"
        mock_mlflow.get_experiment_by_name.return_value = mock_experiment

        with tempfile.TemporaryDirectory() as tmpdir:
            results_path = Path(tmpdir) / "results.json"
            dpo_item = {
                "item": {"workload_id": "w1", "client_id": "c1", "timestamp": 123},
                "sample": {"response": {"model": "test-model", "usage": {"total_tokens": 100}}},
                "metrics": {
                    "dpo-preference-alignment": {"scores": {"preference_score": {"value": 0.8}}}
                },
            }
            results_path.write_text(json.dumps({"dpo-judge": [dpo_item]}))

            result = client.upload_result(
                results_path=results_path,
                flywheel_run_id="fw-123",
                model_name="test/model",
                eval_type="dpo-eval",
                workload_type=WorkloadClassification.DPO,
            )

        assert result is not None

    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_upload_result_generic_workload_else_branch(self, mock_mlflow):
        """Test upload_result with generic workload (else branch)."""
        # Lines 228-229 coverage: else branch for task_key
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "exp-id"
        mock_mlflow.get_experiment_by_name.return_value = mock_experiment

        with tempfile.TemporaryDirectory() as tmpdir:
            results_path = Path(tmpdir) / "results.json"
            generic_item = {
                "item": {"workload_id": "w1", "client_id": "c1", "timestamp": 123},
                "sample": {"response": {"model": "test-model", "usage": {"total_tokens": 100}}},
                "metrics": {"llm-judge": {"scores": {"similarity": {"value": 0.9}}}},
            }
            results_path.write_text(json.dumps({"llm-as-judge": [generic_item]}))

            result = client.upload_result(
                results_path=results_path,
                flywheel_run_id="fw-123",
                model_name="test/model",
                eval_type="base-eval",
                workload_type=WorkloadClassification.GENERIC,
            )

        assert result is not None

    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_upload_result_dpo_metrics_columns(self, mock_mlflow):
        """Test that DPO metrics columns are correctly defined."""
        # Lines 291-297 coverage: DPO metric_columns
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "exp-id"
        mock_mlflow.get_experiment_by_name.return_value = mock_experiment

        with tempfile.TemporaryDirectory() as tmpdir:
            results_path = Path(tmpdir) / "results.json"
            dpo_items = [
                {
                    "item": {"workload_id": "w1", "client_id": "c1", "timestamp": i},
                    "sample": {
                        "response": {
                            "model": "test-model",
                            "usage": {
                                "total_tokens": 100 + i,
                                "prompt_tokens": 50,
                                "completion_tokens": 50 + i,
                            },
                        }
                    },
                    "metrics": {
                        "dpo-preference-alignment": {
                            "scores": {"preference_score": {"value": 0.7 + i * 0.1}}
                        }
                    },
                }
                for i in range(3)
            ]
            results_path.write_text(json.dumps({"dpo-judge": dpo_items}))

            client.upload_result(
                results_path=results_path,
                flywheel_run_id="fw-123",
                model_name="test/model",
                eval_type="dpo-eval",
                workload_type=WorkloadClassification.DPO,
            )

        # Should log step metrics
        assert mock_mlflow.log_metrics.called

    @patch("src.lib.integration.mlflow_client.mlflow")
    def test_upload_result_generic_metrics_columns_else(self, mock_mlflow):
        """Test that generic metrics columns are correctly defined (else branch)."""
        # Lines 298-299 coverage: else branch for metric_columns
        mlflow_config = MLflowConfig(enabled=True)
        client = MLflowClient(mlflow_config)

        mock_experiment = MagicMock()
        mock_experiment.experiment_id = "exp-id"
        mock_mlflow.get_experiment_by_name.return_value = mock_experiment

        with tempfile.TemporaryDirectory() as tmpdir:
            results_path = Path(tmpdir) / "results.json"
            items = [
                {
                    "item": {"workload_id": "w1", "client_id": "c1", "timestamp": i},
                    "sample": {
                        "response": {
                            "model": "test-model",
                            "usage": {
                                "total_tokens": 100 + i,
                                "prompt_tokens": 50,
                                "completion_tokens": 50 + i,
                            },
                        }
                    },
                    "metrics": {"llm-judge": {"scores": {"similarity": {"value": 0.8 + i * 0.05}}}},
                }
                for i in range(3)
            ]
            results_path.write_text(json.dumps({"llm-as-judge": items}))

            result = client.upload_result(
                results_path=results_path,
                flywheel_run_id="fw-123",
                model_name="test/model",
                eval_type="base-eval",
                workload_type=WorkloadClassification.GENERIC,
            )

        # Should compute and log summary metrics
        assert result is not None
