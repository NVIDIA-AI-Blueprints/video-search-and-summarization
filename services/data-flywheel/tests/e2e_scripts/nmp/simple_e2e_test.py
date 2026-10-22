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

"""
Simplified End-to-End Integration Test for Data Flywheel Blueprint

This test can be run as:
1. Standalone script: python tests/e2e_scripts/simple_e2e_test.py [args]
2. Pytest test: pytest -m e2e_integration tests/e2e_scripts/simple_e2e_test.py

The test performs:
1. Loads test data to Elasticsearch
2. Creates a flywheel job
3. Monitors job progress until completion
4. Verifies model evaluations and customizations

Prerequisites:
- API server and Elasticsearch services accessible and properly configured
- Backend services configured with required API keys (NVIDIA_API_KEY required, NGC_API_KEY for K8s deployments)

Setup:
    # Install uv (if not already installed)
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Install dependencies
    uv sync

    # Pull dataset files from Git LFS
    git lfs pull

Usage:
    # As standalone script (minimal dependencies)
    python tests/e2e_scripts/simple_e2e_test.py [--workload-id WORKLOAD] [--client-id CLIENT] [--timeout MINUTES] [--api-url URL] [--elasticsearch-url URL] [--data-file FILE] [--allow-external-files]

    # With uv (recommended - manages dependencies)
    uv run python tests/e2e_scripts/simple_e2e_test.py [options]

    # As pytest test (requires pytest)
    uv run pytest -m e2e_integration tests/e2e_scripts/simple_e2e_test.py
    uv run pytest -m e2e_integration tests/e2e_scripts/simple_e2e_test.py -s -v

Standalone Installation:
    # Option 1: Using pip (install minimal dependencies)
    pip install requests elasticsearch
    python tests/e2e_scripts/simple_e2e_test.py

    # Option 2: Using uv (recommended - handles all dependencies)
    curl -LsSf https://astral.sh/uv/install.sh | sh
    uv sync
    uv run python tests/e2e_scripts/simple_e2e_test.py
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Optional imports - make pytest optional for standalone usage
try:
    import pytest

    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False

    # Define a dummy pytest.mark for standalone usage
    class _DummyPytest:
        class mark:  # noqa: N801
            @staticmethod
            def e2e_integration(func):
                return func

            @staticmethod
            def slow(func):
                return func

    pytest = _DummyPytest()

import requests
from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk

# Default collection name (matching es_client.py)
ES_COLLECTION_NAME = "flywheel"


def check_dependencies():
    """Check that required dependencies are available for standalone usage."""
    missing_deps = []

    # Check for required packages
    try:
        import requests  # noqa: F401
    except ImportError:
        missing_deps.append("requests")

    try:
        from elasticsearch import Elasticsearch  # noqa: F401
    except ImportError:
        missing_deps.append("elasticsearch")

    if missing_deps:
        print("❌ Missing required dependencies for standalone usage:")
        for dep in missing_deps:
            print(f"   • {dep}")
        print("\nTo install dependencies:")
        print("   pip install requests elasticsearch")
        print("   # OR if using uv:")
        print("   uv sync")
        return False

    return True


def validate_path(file_path: str, allow_external: bool = False) -> Path:
    """
    Validate and resolve a file path safely.

    Args:
        file_path: The file path to validate
        allow_external: If True, allows paths outside the project directory
                       (useful for standalone usage with external data files)

    Returns:
        Path: The safe, resolved path

    Raises:
        ValueError: If the path is invalid or unsafe
        FileNotFoundError: If file doesn't exist
    """
    # Get project root (3 levels up from this script)
    script_path = Path(__file__).resolve()
    project_root = script_path.parent.parent.parent

    # Convert to Path object
    path = Path(file_path)

    # If it's a relative path, resolve relative to project root
    if not path.is_absolute():
        path = project_root / path

    # Resolve the path to handle any '..' or '.' components
    try:
        resolved_path = path.resolve()
    except (OSError, RuntimeError) as e:
        raise ValueError(f"Cannot resolve path '{file_path}': {e}") from e

    # Security check: ensure the resolved path is within the project directory
    # Skip this check if allow_external=True (for standalone usage)
    if not allow_external:
        try:
            resolved_path.relative_to(project_root)
        except ValueError as e:
            raise ValueError(
                f"Path '{file_path}' resolves outside the project directory. "
                f"Only paths within the project are allowed for security. "
                f"Use allow_external=True to override this check."
            ) from e

    # Check if file exists
    if not resolved_path.exists():
        raise FileNotFoundError(f"File not found: {resolved_path}")

    return resolved_path


def create_openai_request_response(data: dict[str, Any]) -> dict[str, Any]:
    """Transform the data into an OpenAI-style request/response pair."""
    # Validate that messages exist and are not empty
    if "messages" not in data:
        raise ValueError("Missing 'messages' key in data")

    if not isinstance(data["messages"], list):
        raise ValueError("'messages' must be a list")

    if len(data["messages"]) == 0:
        raise ValueError("'messages' list cannot be empty")

    if len(data["messages"]) < 2:
        raise ValueError(
            "'messages' list must contain at least 2 messages for proper request/response structure"
        )

    # Create a timestamp for the request
    timestamp = int(datetime.utcnow().timestamp())

    # Create the request structure
    request = {
        "model": "not-a-model",
        "messages": data["messages"][:-1],
        "temperature": 0.7,
        "max_tokens": 1000,
    }

    if data.get("tools"):
        request["tools"] = data["tools"]

    # Create the response structure
    response = {
        "id": f"chatcmpl-{timestamp}",
        "object": "chat.completion",
        "created": timestamp,
        "model": "not-a-model",
        "choices": [
            {
                "index": 0,
                "message": data["messages"][-1],
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": len(data["messages"][0]["content"].split()),
            "completion_tokens": len(data["messages"][1]["content"].split()),
            "total_tokens": len(data["messages"][0]["content"].split())
            + len(data["messages"][1]["content"].split()),
        },
    }

    return {"timestamp": timestamp, "request": request, "response": response}


class SimpleE2ETest:
    """Simplified E2E Integration Test focusing on job execution and monitoring"""

    def __init__(
        self,
        workload_id: str = "e2e-test",
        client_id: str = "test-client",
        timeout_minutes: int = 90,
        api_url: str | None = None,
        elasticsearch_url: str | None = None,
        data_file: str = "data/aiva_primary_assistant_dataset.jsonl",
        standalone_mode: bool = False,
    ):
        # Set default URLs
        if api_url is None:
            self.api_base_url = "http://localhost:8000"
        else:
            self.api_base_url = api_url

        if elasticsearch_url is None:
            self.elasticsearch_url = "http://localhost:9200"
        else:
            self.elasticsearch_url = elasticsearch_url

        self.workload_id = workload_id
        self.client_id = client_id
        self.timeout_minutes = timeout_minutes
        self.data_file = data_file
        self.standalone_mode = standalone_mode
        self.job_id = None

        # Get project root
        script_path = Path(__file__).resolve()
        self.project_root = script_path.parent.parent.parent

        print("🚀 Starting Simplified E2E Integration Test")
        print(f"   API URL: {self.api_base_url}")
        print(f"   Elasticsearch URL: {self.elasticsearch_url}")
        print(f"   Workload ID: {self.workload_id}")
        print(f"   Client ID: {self.client_id}")
        print(f"   Timeout: {self.timeout_minutes} minutes")

        # Show both the provided file path and resolved path
        try:
            resolved_path = validate_path(self.data_file, allow_external=self.standalone_mode)
            print(f"   Data File: {self.data_file}")
            print(f"   Resolved Path: {resolved_path}")
        except (ValueError, FileNotFoundError) as e:
            print(
                f"   Data File: {self.data_file} (⚠️  Path validation will be checked during load: {e})"
            )

    def log(self, message: str, level: str = "INFO"):
        """Log a message with timestamp"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] [{level}] {message}")

    def check_services(self):
        """Check that required services are running"""
        self.log("Checking service availability...")

        services_to_check = [
            ("API Server", f"{self.api_base_url}/api/jobs"),
            ("Elasticsearch", f"{self.elasticsearch_url}/_cluster/health"),
        ]

        for service_name, url in services_to_check:
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    self.log(f"✅ {service_name} is available")
                else:
                    self.log(f"❌ {service_name} returned status {response.status_code}", "ERROR")
                    return False
            except requests.exceptions.RequestException as e:
                self.log(f"❌ {service_name} is not accessible: {e}", "ERROR")
                return False

        return True

    def load_test_data(self):
        """Load test data to Elasticsearch using custom URL"""
        # Validate and get the full path for logging
        try:
            resolved_path = validate_path(self.data_file, allow_external=self.standalone_mode)
            self.log(f"Loading test data to Elasticsearch from: {resolved_path}")
        except (ValueError, FileNotFoundError) as e:
            self.log(f"❌ Invalid data file path '{self.data_file}': {e}", "ERROR")
            raise

        try:
            self._load_data_to_elasticsearch(
                workload_id=self.workload_id, client_id=self.client_id, file_path=self.data_file
            )
            self.log(f"✅ Test data loaded successfully from {resolved_path}")
        except Exception as e:
            self.log(f"❌ Failed to load test data from {resolved_path}: {e}", "ERROR")
            raise

    def _load_data_to_elasticsearch(
        self,
        workload_id: str = "",
        client_id: str = "",
        file_path: str = "data/aiva_primary_assistant_dataset.jsonl",
        index_name: str = ES_COLLECTION_NAME,
    ):
        """Load test data from JSON file into Elasticsearch with custom URL."""
        # Initialize Elasticsearch client with custom URL
        es = Elasticsearch(hosts=[self.elasticsearch_url])

        # Test connection
        self.log(f"Testing Elasticsearch connection to {self.elasticsearch_url}...")
        if not es.ping():
            raise RuntimeError(f"Cannot connect to Elasticsearch at {self.elasticsearch_url}")

        # Check cluster health
        try:
            health = es.cluster.health()
            self.log(f"Elasticsearch cluster status: {health.get('status', 'unknown')}")
        except Exception as e:
            self.log(f"⚠️  Could not get cluster health: {e}", "WARNING")

        # Ensure index exists
        self.log(f"Checking if index '{index_name}' exists...")
        if not es.indices.exists(index=index_name):
            self.log(f"Creating index '{index_name}'...")
            # Create basic index
            es.indices.create(index=index_name)
        else:
            self.log(f"Index '{index_name}' already exists")

        # Validate and get the safe path
        safe_path = validate_path(file_path, allow_external=self.standalone_mode)

        # Read the test data
        with open(safe_path) as f:
            test_data = [json.loads(line) for line in f]

        if test_data and test_data[0].get("workload_id"):
            # Document is already in the correct log format. However, for repeatable
            # integration tests we want the ability to override the `workload_id`
            # and `client_id` so that search queries scoped to those dynamic values
            # will find the freshly-loaded records. When callers provide non-empty
            # workload_id/client_id arguments we overwrite the existing values.

            self.log(
                f"Document is already in the log format. Loading {len(test_data)} records with overrides..."
            )

            # Use bulk indexing for better performance
            documents = []
            for i, doc in enumerate(test_data):
                if i > 0 and i % 100 == 0:
                    self.log(f"Processing document {i}/{len(test_data)}...")

                # Ensure we do not mutate the original dict across iterations
                indexed_doc = dict(doc)

                # Override identifiers if provided by caller. This allows the
                # integration tests to generate unique IDs while reusing a static
                # JSONL fixture on disk.
                if workload_id:
                    indexed_doc["workload_id"] = workload_id
                if client_id:
                    indexed_doc["client_id"] = client_id

                documents.append({"_index": index_name, "_source": indexed_doc})

                # Bulk index in chunks of 100 documents
                if len(documents) >= 100:
                    self._bulk_index_documents(es, documents)
                    documents = []

            # Index remaining documents
            if documents:
                self._bulk_index_documents(es, documents)
        else:
            # Document is not in the correct format, so we need to transform it
            self.log(f"Transforming and loading {len(test_data)} records...")

            documents = []
            for i, item in enumerate(test_data):
                if i > 0 and i % 100 == 0:
                    self.log(f"Transforming document {i}/{len(test_data)}...")

                # Create OpenAI-style request/response pair
                doc = create_openai_request_response(item)

                doc["workload_id"] = workload_id

                if client_id:
                    doc["client_id"] = client_id

                documents.append({"_index": index_name, "_source": doc})

                # Bulk index in chunks of 100 documents
                if len(documents) >= 100:
                    self._bulk_index_documents(es, documents)
                    documents = []

            # Index remaining documents
            if documents:
                self._bulk_index_documents(es, documents)

        # Flush the index to disk
        es.indices.flush(index=index_name)

        # Refresh the index to make all operations performed since the last refresh available for search
        es.indices.refresh(index=index_name)

        self.log("Data loaded successfully.")

    def _bulk_index_documents(self, es: Elasticsearch, documents: list):
        """Bulk index a list of documents to Elasticsearch"""
        try:
            # Use the updated API without deprecated parameters
            es_with_options = es.options(request_timeout=60)
            success, failed = bulk(es_with_options, documents, chunk_size=100)
            if failed:
                self.log(f"⚠️  Failed to index {len(failed)} documents", "WARNING")
                # Log first few failures for debugging
                for fail in failed[:3]:
                    self.log(f"   Failed doc: {fail}", "WARNING")
            else:
                self.log(f"✅ Successfully indexed {success} documents")
        except Exception as e:
            self.log(f"❌ Bulk indexing error: {e}", "ERROR")
            raise

    def create_job(self):
        """Create a new flywheel job"""
        self.log("Creating flywheel job...")

        # Define data split configuration as specified
        data_split_config = {
            "eval_size": 20,
            "val_ratio": 0.3,
            "min_total_records": 50,
            "random_seed": None,
            "limit": 100,
            "parse_function_arguments": True,
        }

        job_request = {
            "workload_id": self.workload_id,
            "client_id": self.client_id,
            "data_split_config": data_split_config,
        }

        try:
            response = requests.post(f"{self.api_base_url}/api/jobs", json=job_request, timeout=30)

            if response.status_code != 200:
                self.log(
                    f"❌ Failed to create job: {response.status_code} - {response.text}", "ERROR"
                )
                raise RuntimeError(f"Job creation failed: {response.text}")

            job_data = response.json()
            self.job_id = job_data["id"]

            self.log(f"✅ Job created successfully: {self.job_id}")
            return self.job_id

        except requests.exceptions.RequestException as e:
            self.log(f"❌ Failed to create job: {e}", "ERROR")
            raise

    def get_job_status(self):
        """Get current job status"""
        try:
            response = requests.get(f"{self.api_base_url}/api/jobs/{self.job_id}", timeout=30)

            if response.status_code != 200:
                self.log(f"❌ Failed to get job status: {response.status_code}", "ERROR")
                return None

            return response.json()

        except requests.exceptions.RequestException as e:
            self.log(f"❌ Error getting job status: {e}", "ERROR")
            return None

    def log_job_progress(self, job_data: dict[str, Any]):
        """Log detailed job progress"""
        status = job_data.get("status", "unknown")
        num_records = job_data.get("num_records", 0)

        # Calculate elapsed time
        started_at = job_data.get("started_at")
        if started_at:
            try:
                start_time = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                elapsed = datetime.now().replace(tzinfo=start_time.tzinfo) - start_time
                elapsed_str = str(elapsed).split(".")[0]
                self.log(
                    f"📊 Job Status: {status} | Records: {num_records} | Elapsed: {elapsed_str}"
                )
            except Exception:
                self.log(f"📊 Job Status: {status} | Records: {num_records}")
        else:
            self.log(f"📊 Job Status: {status} | Records: {num_records}")

        # Log NIM progress
        nims = job_data.get("nims", [])
        for i, nim in enumerate(nims, 1):
            model_name = nim["model_name"]
            nim_status = nim["status"]
            deployment_status = nim.get("deployment_status", "unknown")

            self.log(f"  🤖 NIM {i}: {model_name}")
            self.log(f"     Status: {nim_status} | Deployment: {deployment_status}")

            # Log evaluations
            evaluations = nim.get("evaluations", [])
            if evaluations:
                self.log(f"     📈 Evaluations ({len(evaluations)}):")
                for eval_data in evaluations:
                    eval_type = eval_data["eval_type"]
                    progress = eval_data["progress"]
                    finished = "✅" if eval_data.get("finished_at") else "🔄"
                    self.log(f"       {finished} {eval_type.upper()}: {progress:.1f}%")

            # Log customizations
            customizations = nim.get("customizations", [])
            if customizations:
                self.log(f"     🎯 Customizations ({len(customizations)}):")
                for custom_data in customizations:
                    progress = custom_data["progress"]
                    epochs = custom_data.get("epochs_completed", 0)
                    steps = custom_data.get("steps_completed", 0)
                    finished = "✅" if custom_data.get("finished_at") else "🔄"
                    self.log(
                        f"       {finished} Fine-tuning: {progress:.1f}% (epochs: {epochs}, steps: {steps})"
                    )

        # Log LLM Judge status
        llm_judge = job_data.get("llm_judge")
        if llm_judge:
            judge_status = llm_judge.get("deployment_status", "unknown")
            self.log(f"  ⚖️  LLM Judge: {judge_status}")

    def monitor_job(self):
        """Monitor job until completion"""
        self.log(f"🔍 Monitoring job {self.job_id} (timeout: {self.timeout_minutes} minutes)...")

        start_time = time.time()
        timeout_seconds = self.timeout_minutes * 60
        poll_interval = 30  # Check every 30 seconds

        last_status = None

        while time.time() - start_time < timeout_seconds:
            job_data = self.get_job_status()

            if job_data is None:
                self.log("⚠️  Failed to get job status, retrying...", "WARNING")
                time.sleep(poll_interval)
                continue

            current_status = job_data["status"]

            # Log progress (always show, but with different detail levels)
            if current_status != last_status:
                self.log(f"🔄 Status changed: {last_status} → {current_status}")
                last_status = current_status

            self.log_job_progress(job_data)

            # Check if job is complete
            if current_status in ["completed", "failed", "cancelled"]:
                if current_status == "completed":
                    self.log("🎉 Job completed successfully!")
                    return job_data
                else:
                    error_msg = job_data.get("error", "Unknown error")
                    self.log(f"❌ Job failed with status '{current_status}': {error_msg}", "ERROR")
                    return job_data

            # Wait before next check
            self.log(f"⏳ Waiting {poll_interval} seconds before next check...")
            time.sleep(poll_interval)

        # Timeout reached
        self.log(f"⏰ Job monitoring timed out after {self.timeout_minutes} minutes", "ERROR")
        return self.get_job_status()  # Get final status

    def verify_results(self, job_data: dict[str, Any]):
        """Verify job completed successfully with expected results"""
        self.log("🔍 Verifying job results...")

        success = True
        issues = []

        # Check job completion
        status = job_data.get("status")
        if status != "completed":
            issues.append(f"Job status is '{status}', expected 'completed'")
            success = False

        # Check basic metrics
        num_records = job_data.get("num_records", 0)
        if num_records == 0:
            issues.append("No records were processed")
            success = False

        if not job_data.get("finished_at"):
            issues.append("Job has no finish time")
            success = False

        # Check NIMs
        nims = job_data.get("nims", [])
        if len(nims) == 0:
            issues.append("No NIMs found in job results")
            success = False

        for i, nim in enumerate(nims, 1):
            model_name = nim["model_name"]
            self.log(f"  🤖 Verifying NIM {i}: {model_name}")

            # Check NIM status
            nim_status = nim.get("status")
            if nim_status not in ["completed", "running-evals"]:
                issues.append(f"NIM {model_name} has unexpected status: {nim_status}")
                success = False

            deployment_status = nim.get("deployment_status")
            if deployment_status != "completed":
                issues.append(f"NIM {model_name} deployment not completed: {deployment_status}")
                success = False

            # Check evaluations
            evaluations = nim.get("evaluations", [])
            if len(evaluations) < 2:
                issues.append(
                    f"NIM {model_name} has insufficient evaluations: {len(evaluations)} (expected ≥2)"
                )
                success = False

            eval_types_found = set()
            for eval_data in evaluations:
                eval_type = eval_data["eval_type"]
                eval_types_found.add(eval_type)

                # Check evaluation completion
                progress = eval_data.get("progress", 0)
                if progress != 100.0:
                    issues.append(
                        f"Evaluation {eval_type} for {model_name} not complete: {progress}%"
                    )
                    success = False

                if not eval_data.get("finished_at"):
                    issues.append(f"Evaluation {eval_type} for {model_name} has no finish time")
                    success = False

                if not eval_data.get("scores"):
                    issues.append(f"Evaluation {eval_type} for {model_name} has no scores")
                    success = False

                runtime = eval_data.get("runtime_seconds", 0)
                if runtime <= 0:
                    issues.append(f"Evaluation {eval_type} for {model_name} has no runtime")
                    success = False

                self.log(f"    ✅ Evaluation {eval_type}: {progress}% complete, {runtime}s runtime")

            # Verify required evaluation types
            if "base-eval" not in eval_types_found:
                issues.append(f"Missing base evaluation for {model_name}")
                success = False

            if "icl-eval" not in eval_types_found:
                issues.append(f"Missing ICL evaluation for {model_name}")
                success = False

            # Check customizations (if any)
            customizations = nim.get("customizations", [])
            has_customized_eval = "customized-eval" in eval_types_found

            if customizations:
                self.log(f"    🎯 Found {len(customizations)} customization(s)")

                for custom_data in customizations:
                    progress = custom_data.get("progress", 0)
                    if progress != 100.0:
                        issues.append(f"Customization for {model_name} not complete: {progress}%")
                        success = False

                    if not custom_data.get("finished_at"):
                        issues.append(f"Customization for {model_name} has no finish time")
                        success = False

                    epochs = custom_data.get("epochs_completed", 0)
                    steps = custom_data.get("steps_completed", 0)

                    if epochs <= 0:
                        issues.append(f"Customization for {model_name} completed no epochs")
                        success = False

                    if steps <= 0:
                        issues.append(f"Customization for {model_name} completed no steps")
                        success = False

                    self.log(
                        f"    ✅ Customization: {progress}% complete, {epochs} epochs, {steps} steps"
                    )

                # If there are customizations, there should be a customized evaluation
                if not has_customized_eval:
                    issues.append(
                        f"Missing customized evaluation for {model_name} (customization completed)"
                    )
                    success = False

            if has_customized_eval and not customizations:
                issues.append(
                    f"Found customized evaluation for {model_name} but no customization record"
                )
                success = False

        # Check LLM Judge
        llm_judge = job_data.get("llm_judge")
        if llm_judge:
            judge_status = llm_judge.get("deployment_status")
            if judge_status not in ["completed", "ready"]:
                issues.append(f"LLM judge deployment not completed: {judge_status}")
                success = False
            else:
                self.log(f"  ✅ LLM Judge: {judge_status}")

        # Report results
        if success:
            self.log("🎉 All verification checks passed!")

            # Print summary
            self.log("\n📋 SUMMARY:")
            self.log(f"  Job ID: {job_data.get('id')}")
            self.log(f"  Status: {job_data.get('status')}")
            self.log(f"  Records Processed: {job_data.get('num_records')}")
            self.log(f"  NIMs Tested: {len(nims)}")

            total_evaluations = sum(len(nim.get("evaluations", [])) for nim in nims)
            total_customizations = sum(len(nim.get("customizations", [])) for nim in nims)

            self.log(f"  Total Evaluations: {total_evaluations}")
            self.log(f"  Total Customizations: {total_customizations}")

            if job_data.get("started_at") and job_data.get("finished_at"):
                try:
                    start_time = datetime.fromisoformat(
                        job_data["started_at"].replace("Z", "+00:00")
                    )
                    end_time = datetime.fromisoformat(
                        job_data["finished_at"].replace("Z", "+00:00")
                    )
                    total_time = end_time - start_time
                    self.log(f"  Total Runtime: {str(total_time).split('.')[0]}")
                except (ValueError, TypeError, KeyError, AttributeError) as e:
                    self.log(f"  Could not calculate runtime: {e}", "WARNING")
                    pass

        else:
            self.log("❌ Verification failed with the following issues:", "ERROR")
            for issue in issues:
                self.log(f"  • {issue}", "ERROR")

        return success

    def cleanup_test_data(self):
        """Clean up test data from Elasticsearch"""
        self.log("🧹 Cleaning up test data...")

        try:
            delete_query = {
                "query": {
                    "bool": {
                        "must": [
                            {"match": {"workload_id": self.workload_id}},
                            {"match": {"client_id": self.client_id}},
                        ]
                    }
                }
            }

            response = requests.post(
                f"{self.elasticsearch_url}/{ES_COLLECTION_NAME}/_delete_by_query",
                json=delete_query,
                timeout=30,
            )

            if response.status_code == 200:
                result = response.json()
                deleted_count = result.get("deleted", 0)
                self.log(f"✅ Cleaned up {deleted_count} test records")
            else:
                self.log(f"⚠️  Cleanup response: {response.status_code}", "WARNING")

        except Exception as e:
            self.log(f"⚠️  Failed to cleanup test data: {e}", "WARNING")

    def run_test(self):
        """Run the complete integration test"""
        try:
            # Step 1: Check services
            if not self.check_services():
                self.log("❌ Required services are not available", "ERROR")
                return False

            # Step 2: Load test data
            self.load_test_data()

            # Step 3: Create job
            self.create_job()

            # Step 4: Monitor job
            job_data = self.monitor_job()

            if job_data is None:
                self.log("❌ Failed to get final job status", "ERROR")
                return False

            # Step 5: Verify results
            success = self.verify_results(job_data)

            if success:
                self.log("🎉 Integration Test PASSED! 🎉")
            else:
                self.log("❌ Integration Test FAILED! ❌", "ERROR")

            return success

        except Exception as e:
            self.log(f"❌ Test failed with exception: {e}", "ERROR")
            return False

        finally:
            # Always cleanup test data
            self.cleanup_test_data()


# Pytest Integration
@pytest.mark.e2e_integration
@pytest.mark.slow
class TestE2EIntegration:
    """Pytest wrapper for the E2E integration test"""

    def test_full_e2e_workflow(self, e2e_config):
        """
        Test the complete end-to-end Data Flywheel workflow

        This test:
        1. Checks service availability
        2. Loads test data to Elasticsearch
        3. Creates a flywheel job
        4. Monitors job until completion
        5. Verifies all model evaluations and customizations
        6. Cleans up test data

        Args:
            e2e_config: Configuration fixture from conftest.py
        """
        # Create test instance with configuration from pytest
        # For pytest, we keep security checks enabled (standalone_mode=False)
        test = SimpleE2ETest(**e2e_config, standalone_mode=False)

        # Run the full test
        success = test.run_test()

        # Assert success for pytest
        assert success, "E2E integration test failed"


def main():
    """Main entry point for standalone usage"""
    # Check dependencies before parsing arguments
    if not check_dependencies():
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Simplified E2E Integration Test")
    parser.add_argument(
        "--workload-id", default="primary_assistant", help="Workload ID for test data"
    )
    parser.add_argument("--client-id", default="client_id", help="Client ID for test data")
    parser.add_argument("--timeout", type=int, default=90, help="Timeout in minutes")
    parser.add_argument("--api-url", help="API server URL (default: http://localhost:8000)")
    parser.add_argument(
        "--elasticsearch-url",
        help="Elasticsearch URL (default: http://localhost:9200)",
    )
    parser.add_argument(
        "--data-file",
        default="data/aiva_primary_assistant_dataset.jsonl",
        help="Data file path to load. Can be relative to project root or absolute path (default: data/aiva_primary_assistant_dataset.jsonl)",
    )
    parser.add_argument(
        "--allow-external-files",
        action="store_true",
        help="Allow data files outside the project directory (useful for testing with external datasets)",
    )

    args = parser.parse_args()

    # Run test
    test = SimpleE2ETest(
        workload_id=args.workload_id,
        client_id=args.client_id,
        timeout_minutes=args.timeout,
        api_url=args.api_url,
        elasticsearch_url=args.elasticsearch_url,
        data_file=args.data_file,
        standalone_mode=args.allow_external_files,
    )

    success = test.run_test()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
