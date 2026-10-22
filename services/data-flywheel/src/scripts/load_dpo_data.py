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

"""Script to load DPO (Direct Preference Optimization) training data into Elasticsearch.

DPO data format consists of:
- prompt: The input prompt (can be a conversation format)
- chosen_response: The preferred/better response
- rejected_response: The less preferred/worse response

This data is used to train models using Direct Preference Optimization.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add both the project root and src directory to the path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)
sys.path.insert(0, src_dir)

from elasticsearch.helpers import bulk  # noqa: E402
from lib.integration.es_client import ES_DPO_DATA_COLLECTION_NAME, get_es_client  # noqa: E402

from src.scripts.utils import validate_path  # noqa: E402


def load_dpo_data_to_elasticsearch(
    workload_id: str | None = None,
    client_id: str | None = None,
    file_path: str | Path = "dpo_training_data.jsonl",
    index_name: str = ES_DPO_DATA_COLLECTION_NAME,
    limit: int | None = None,
):
    """Load DPO preference data from JSON/JSONL file into Elasticsearch.

    Args:
        workload_id: Unique identifier for the workload (optional, can be in data)
        client_id: Unique identifier for the client (optional, can be in data)
        file_path: Path to the DPO data file (JSONL format)
        index_name: Elasticsearch index name
        limit: Optional limit on number of records to load

    DPO Data Format:
        {
            "prompt": [{"role": "user", "content": "..."}],
            "chosen_response": "The better response...",
            "rejected_response": "The worse response...",
            "workload_id": "optional-workload-id",  // optional, used if not provided via args
            "client_id": "optional-client-id"       // optional, used if not provided via args
        }
    """

    es = get_es_client()

    # Handle Path objects and strings
    if isinstance(file_path, Path):
        if file_path.exists():
            safe_path = str(file_path)
        else:
            raise FileNotFoundError(f"File not found: {file_path}")
    elif os.path.isabs(file_path):
        if os.path.exists(file_path):
            safe_path = file_path
        else:
            raise FileNotFoundError(f"File not found: {file_path}")
    else:
        # For relative paths, use validate_path
        safe_path = validate_path(file_path, is_input=True, data_dir="data")

    # Read data - support JSON array or JSONL
    with open(safe_path) as f:
        content = f.read().strip()
        try:
            data = json.loads(content)
            if not isinstance(data, list):
                data = [data]
        except json.JSONDecodeError:
            # Parse as JSONL
            data = [json.loads(line) for line in content.split("\n") if line.strip()]

    # Track original count for reporting
    original_count = len(data)

    # Validate and apply limit
    if limit is not None and limit <= 0:
        raise ValueError(f"--limit must be a positive integer, got: {limit}")

    truncated = False
    if limit is not None:
        if limit < original_count:
            data = data[:limit]
            truncated = True
            print(f"📊 Loaded {original_count} records from file")
            print(
                f"⚠️  Applying limit: Will process first {limit} records "
                f"({original_count - limit} records truncated)"
            )
        else:
            print(f"📊 Loaded {original_count} records from file")
            print(f"   Note: --limit {limit} specified, but file has fewer records")
    else:
        print(f"📊 Loaded {original_count} records from file")

    print(f"\n🔄 Processing {len(data)} DPO records...")

    bulk_actions = []
    skipped = 0

    for item in data:
        try:
            timestamp = int(datetime.utcnow().timestamp())

            # Validate DPO data format
            if not isinstance(item, dict):
                print(f"Skipping invalid record: not a dictionary. Record type: {type(item)}")
                skipped += 1
                continue

            # Required fields for DPO
            required_fields = ["prompt", "chosen_response", "rejected_response"]
            missing_fields = [field for field in required_fields if field not in item]

            if missing_fields:
                print(
                    f"Skipping invalid record: missing required fields {missing_fields}. Record: {item}"
                )
                skipped += 1
                continue

            # Validate prompt format (should be list of message dicts or string)
            prompt = item["prompt"]
            if isinstance(prompt, str):
                # Convert string prompt to conversation format
                prompt = [{"role": "user", "content": prompt}]
            elif not isinstance(prompt, list):
                print(f"Skipping invalid record: prompt must be string or list. Record: {item}")
                skipped += 1
                continue

            # Determine workload_id: use argument if provided, otherwise use from data
            record_workload_id = workload_id or item.get("workload_id")
            if not record_workload_id:
                print(
                    f"Skipping invalid record: workload_id not provided via argument or in data. Record: {item}"
                )
                skipped += 1
                continue

            # Determine client_id: use argument if provided, otherwise use from data
            record_client_id = client_id or item.get("client_id")

            # Create indexed document
            indexed_doc = {
                "workload_id": record_workload_id,
                "timestamp": timestamp,
                "prompt": prompt,
                "chosen_response": item["chosen_response"],
                "rejected_response": item["rejected_response"],
            }

            if record_client_id:
                indexed_doc["client_id"] = record_client_id

            # Add to bulk actions
            bulk_actions.append({"_index": index_name, "_source": indexed_doc})

        except (ValueError, KeyError) as e:
            print(f"Data validation error for record {item}: {e}")
            skipped += 1
            continue
        except Exception as e:
            print(f"Unexpected error processing record {item}: {e}")
            skipped += 1
            continue

    # Bulk index all documents
    successful = 0
    if bulk_actions:
        print(f"Bulk indexing {len(bulk_actions)} DPO records...")
        try:
            success_count, failed_items = bulk(
                es,
                bulk_actions,
                stats_only=False,
                chunk_size=1000,
                request_timeout=60,
            )
            successful = success_count
            print(f"Successfully indexed {success_count} records")
            if failed_items:
                print(f"Warning: {len(failed_items)} records failed to index")
        except Exception as e:
            print(f"Error during bulk indexing: {e}")
            raise

    # Flush and refresh
    es.indices.flush(index=index_name)
    es.indices.refresh(index=index_name)

    print("\n" + "=" * 70)
    print("✅ DPO Data Loading Summary")
    print("=" * 70)
    print(f"📁 Original file records:     {original_count}")
    if truncated:
        print(
            f"✂️  Limit applied:              {limit} (truncated {original_count - limit} records)"
        )
    print(f"🔄 Records processed:         {len(data)}")
    print(f"⏭️  Skipped (validation):      {skipped}")
    print(f"✅ Successfully indexed:      {successful}")
    if failed_items:
        print(f"❌ Failed to index:           {len(failed_items)}")
    print(f"📍 Index:                     {index_name}")
    if workload_id:
        print(f"🏷️  Workload ID:               {workload_id}")
    else:
        print("🏷️  Workload ID:               (from data records)")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Load DPO (Direct Preference Optimization) training data into Elasticsearch."
    )
    parser.add_argument(
        "--workload-id",
        default=None,
        help="Unique identifier for the workload (optional, can be in data records)",
    )
    parser.add_argument(
        "--client-id",
        default=None,
        help="Unique identifier for the client (optional, can be in data records)",
    )
    parser.add_argument(
        "--file",
        default="dpo_training_data.jsonl",
        help="Input JSONL file path with DPO data. Relative paths are resolved from the data/ directory. "
        "Absolute paths are also supported. (default: dpo_training_data.jsonl)",
    )
    parser.add_argument(
        "--index-name",
        default=ES_DPO_DATA_COLLECTION_NAME,
        help=f"Elasticsearch index name (default: {ES_DPO_DATA_COLLECTION_NAME})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Optional positive integer limit on number of records to load "
            "(default: unlimited - loads all records)"
        ),
    )

    args = parser.parse_args()

    try:
        load_dpo_data_to_elasticsearch(
            workload_id=args.workload_id,
            client_id=args.client_id,
            file_path=args.file,
            index_name=args.index_name,
            limit=args.limit,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
