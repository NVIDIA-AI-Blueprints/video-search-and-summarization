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
import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

# Add both the project root and src directory to the path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)
sys.path.insert(0, src_dir)

from elasticsearch.helpers import bulk  # noqa: E402
from lib.integration.es_client import ES_COLLECTION_NAME, get_es_client  # noqa: E402

from src.scripts.utils import validate_path  # noqa: E402

ES_CLIENT = get_es_client()


def _sanitize_tools(tools: Any) -> Any:
    """Normalize tool parameter schemas for NMP 26.03's tool-calling evaluator.

    Two transformations are applied per record:

    1. **Strip ``None`` property values.** Some bundled DFW datasets (e.g.
       ``aiva_primary_assistant_dataset.jsonl``) encode optional parameters as
       ``{name: None}`` inside ``function.parameters.properties``. NMP 26.03's
       small instruct NIMs (llama-3.2-1b 1.8.x) iterate that dict to validate
       the schema and crash with ``500 - argument of type 'NoneType' is not
       iterable``.

    2. **Pre-merge the property-name union across all tools in the record.**
       NMP 26.03's tool-calling evaluator pod unions all property names across
       the tools[] array before sending the request, filling in ``None`` for
       properties that a given tool does not define. Outlines (the guided
       decoding backend) then walks each tool's schema and crashes when it
       encounters those ``None`` values. By pre-merging here — so every tool
       already has every property declared with its real (non-null) schema —
       the evaluator's union step has nothing to fill, and outlines walks
       only well-typed sub-schemas.
    """
    if not isinstance(tools, list):
        return tools

    # First pass: collect the union of property name -> real schema (use the
    # first non-null definition we see for each name).
    union_props: dict[str, Any] = {}
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        if not isinstance(fn, dict):
            continue
        params = fn.get("parameters")
        if not isinstance(params, dict):
            continue
        props = params.get("properties")
        if not isinstance(props, dict):
            continue
        for k, v in props.items():
            if k not in union_props and v is not None:
                union_props[k] = v

    # Second pass: rewrite each tool so its properties dict contains every
    # union'd name with the real schema (own schema if present, else the
    # canonical union'd schema). Drop the original ``None`` placeholders.
    cleaned: list[Any] = []
    for tool in tools:
        if not isinstance(tool, dict):
            cleaned.append(tool)
            continue
        new_tool = dict(tool)
        fn = new_tool.get("function")
        if isinstance(fn, dict):
            new_fn = dict(fn)
            params = new_fn.get("parameters")
            if isinstance(params, dict):
                new_params = dict(params)
                own_props = new_params.get("properties") or {}
                if isinstance(own_props, dict):
                    merged: dict[str, Any] = {}
                    for k in union_props:
                        if k in own_props and own_props[k] is not None:
                            merged[k] = own_props[k]
                        else:
                            merged[k] = union_props[k]
                    new_params["properties"] = merged
                new_fn["parameters"] = new_params
            new_tool["function"] = new_fn
        cleaned.append(new_tool)
    return cleaned


def _sanitize_record(doc: dict[str, Any]) -> dict[str, Any]:
    """Apply data-quality fixups to a single record before indexing."""
    req = doc.get("request")
    if isinstance(req, dict) and "tools" in req:
        req["tools"] = _sanitize_tools(req["tools"])
    return doc


def create_openai_request_response(data: dict[str, Any]) -> dict[str, Any]:
    """Transform the data into an OpenAI-style request/response pair."""
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


def load_data_to_elasticsearch(
    workload_id: str = "",
    client_id: str = "",
    file_path: str = "aiva_primary_assistant_dataset.jsonl",
    index_name: str = ES_COLLECTION_NAME,
):
    """Load test data from JSON file into Elasticsearch using bulk indexing."""
    # Initialize Elasticsearch client
    es = ES_CLIENT

    # Validate and get the safe path
    safe_path = validate_path(file_path, is_input=True, data_dir="data")

    # Read the test data
    with open(safe_path) as f:
        test_data = [json.loads(line) for line in f]

    print(f"Loading {len(test_data)} documents to Elasticsearch...")

    # Prepare bulk actions
    bulk_actions = []

    if test_data and test_data[0].get("workload_id"):
        # Document is already in the correct log format. However, for repeatable
        # integration tests we want the ability to override the `workload_id`
        # and `client_id` so that search queries scoped to those dynamic values
        # will find the freshly-loaded records. When callers provide non-empty
        # workload_id/client_id arguments we overwrite the existing values.

        print("Document is already in the log format. Loading with overrides.")

        for doc in test_data:
            # Ensure we do not mutate the original dict across iterations
            indexed_doc = dict(doc)

            # Override identifiers if provided by caller. This allows the
            # integration tests to generate unique IDs while reusing a static
            # JSONL fixture on disk.
            if workload_id:
                indexed_doc["workload_id"] = workload_id
            if client_id:
                indexed_doc["client_id"] = client_id

            indexed_doc = _sanitize_record(indexed_doc)
            bulk_actions.append({"_index": index_name, "_source": indexed_doc})
    else:
        # Document is not in the correct format, so we need to transform it
        for item in test_data:
            # Create OpenAI-style request/response pair
            doc = create_openai_request_response(item)

            doc["workload_id"] = workload_id

            if client_id:
                doc["client_id"] = client_id

            doc = _sanitize_record(doc)
            bulk_actions.append({"_index": index_name, "_source": doc})

    # Bulk index all documents
    if bulk_actions:
        print(f"Bulk indexing {len(bulk_actions)} documents...")
        try:
            success_count, failed_items = bulk(
                es,
                bulk_actions,
                stats_only=False,
                chunk_size=1000,
                request_timeout=60,
            )
            print(f"Successfully indexed {success_count} documents")
            if failed_items:
                print(f"Warning: {len(failed_items)} documents failed to index")
        except Exception as e:
            print(f"Error during bulk indexing: {e}")
            raise

    # Flush the index to disk
    es.indices.flush(index=index_name)

    # Refresh the index to make all operations performed since the last refresh available for search
    es.indices.refresh(index=index_name)

    print("Data loaded successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Load test data into Elasticsearch with specified parameters."
    )
    parser.add_argument("--workload-id", help="Unique identifier for the workload")
    parser.add_argument(
        "--file",
        help="Input JSONL file path. Relative paths are resolved from the data/ directory. "
        "Absolute paths are also supported.",
    )
    parser.add_argument("--client-id", help="Optional client identifier")
    parser.add_argument("--index-name", default=ES_COLLECTION_NAME, help="Optional index name")

    args = parser.parse_args()

    load_data_to_elasticsearch(
        workload_id=args.workload_id,
        client_id=args.client_id,
        file_path=args.file,
        index_name=args.index_name,
    )
