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

"""Unified script to load data for all Data Flywheel pipelines.

This script provides a unified interface for loading:
- LLM training data (standard supervised fine-tuning)
- DPO data (Direct Preference Optimization with preference pairs)
- Embedding data (triplet format with query-positive-negative samples)

Usage Examples:
    # Load LLM data
    uv run python src/scripts/load_data.py --type llm \
        --workload-id my-workload \
        --file aiva_primary_assistant_dataset.jsonl

    # Load DPO data
    uv run python src/scripts/load_data.py --type dpo \
        --workload-id dpo-workload \
        --client-id my-client \
        --file my_dpo_dataset.jsonl

    # Load embedding data
    uv run src/scripts/load_data.py --type embedding \
        --workload-id embedding-demo \
        --client-id demo-client  \
        --dataset-type bug_pairs_separated \
        --file notebooks/Core_bugs_renamed.csv \
        --chunk-size 5000 \
        --mappings-file notebooks/Core_bugs-combined.csv

Available dataset types:
  - triplet: JSONL/JSON format with [query, pos_doc, neg_doc] or {query, pos_doc, neg_doc}
  - bug_pairs: Single file with bugid, similar_bug, bugdata, similarbug_data columns
  - bug_pairs_separated: Two files - corpus file (--file) and mappings file (--mappings-file)
"""

import argparse
import os
import sys

# Add both the project root and src directory to the path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)
sys.path.insert(0, src_dir)

from src.scripts.load_dpo_data import load_dpo_data_to_elasticsearch  # noqa: E402
from src.scripts.load_embedding_data import load_embedding_data_to_elasticsearch  # noqa: E402
from src.scripts.load_embedding_eval_data import (  # noqa: E402
    load_embedding_eval_data_to_elasticsearch,
)
from src.scripts.load_test_data import load_data_to_elasticsearch  # noqa: E402


def print_usage_examples():
    """Print usage examples for different data types."""
    examples = """
Usage Examples:

1. Load LLM Training Data (Standard Fine-tuning):
   uv run python src/scripts/load_data.py --type llm \\
       --workload-id primary_assistant \\
       --client-id aiva-1 \\
       --file aiva_primary_assistant_dataset.jsonl

2. Load DPO Data (Preference Optimization):
   uv run python src/scripts/load_data.py --type dpo \\
       --workload-id dpo-demo \\
       --client-id demo-client \\
       --file my_dpo_dataset.jsonl \\
       --limit 500

3. Load Embedding Data (Retrieval & Similarity):
   uv run src/scripts/load_data.py --type embedding \\
       --workload-id embedding-demo \\
       --client-id demo-client  \\
       --dataset-type bug_pairs_separated \\
       --file notebooks/Core_bugs_renamed.csv \\
       --chunk-size 5000 \\
       --mappings-file notebooks/Core_bugs-combined.csv

4. Load Embedding Evaluation Data (BEIR Format):
   uv run python src/scripts/load_data.py --type embedding-eval \\
       --workload-id val-set-1 \\
       --client-id mozillacorebugs \\
       --data-dir custom-eval-dataset

For more information:
- LLM data format: Refer to docs/get-started/llm-pipeline/completion.md
- DPO data format: See docs/13-dpo-training.md
- Embedding data format: See docs/14-embedding-training.md
"""
    print(examples)


def load_llm_data(args):
    """Load LLM training data using load_test_data.py."""
    print("\n📚 Loading LLM Training Data...")
    print("   Type: Standard supervised fine-tuning")
    print(f"   File: {args.file}")
    print(f"   Workload ID: {args.workload_id}")
    if args.client_id:
        print(f"   Client ID: {args.client_id}")
    print()

    load_data_to_elasticsearch(
        workload_id=args.workload_id or "",
        client_id=args.client_id or "",
        file_path=args.file,
        index_name=args.index_name,
    )


def load_dpo_data(args):
    """Load DPO preference data using load_dpo_data.py."""
    if not args.workload_id:
        print("Error: --workload-id is required for DPO data loading", file=sys.stderr)
        sys.exit(1)

    print("\n📊 Loading DPO Preference Data...")
    print("   Type: Direct Preference Optimization")
    print(f"   File: {args.file}")
    print(f"   Workload ID: {args.workload_id}")
    if args.client_id:
        print(f"   Client ID: {args.client_id}")
    if args.limit:
        print(f"   Limit: {args.limit} records")
    print()

    load_dpo_data_to_elasticsearch(
        workload_id=args.workload_id,
        client_id=args.client_id or "",
        file_path=args.file,
        index_name=args.index_name,
        limit=args.limit,
    )


def load_embedding_data(args):
    """Load embedding training data using load_embedding_data.py."""
    if not args.workload_id:
        print("Error: --workload-id is required for embedding data loading", file=sys.stderr)
        sys.exit(1)

    if not args.client_id:
        print("Error: --client-id is required for embedding data loading", file=sys.stderr)
        sys.exit(1)

    print("\n🔍 Loading Embedding Training Data...")
    print("   Type: Retrieval & similarity (triplet format)")
    print(f"   File: {args.file}")
    print(f"   Workload ID: {args.workload_id}")
    print(f"   Client ID: {args.client_id}")
    print(f"   Dataset Type: {args.dataset_type}")
    if args.mappings_file:
        print(f"   Mappings File: {args.mappings_file}")
    if args.limit:
        print(f"   Limit: {args.limit} records")
    print()

    load_embedding_data_to_elasticsearch(
        workload_id=args.workload_id,
        client_id=args.client_id,
        file_path=args.file,
        dataset_type=args.dataset_type,
        mappings_file_path=args.mappings_file,
        index_name=args.index_name,
        limit=args.limit,
        chunk_size=args.chunk_size,
    )


def load_embedding_eval_data(args):
    """Load embedding evaluation data using load_embedding_eval_data.py."""
    if not args.workload_id:
        print("Error: --workload-id is required for embedding eval data loading", file=sys.stderr)
        sys.exit(1)

    if not args.client_id:
        print("Error: --client-id is required for embedding eval data loading", file=sys.stderr)
        sys.exit(1)

    if not args.data_dir:
        print("Error: --data-dir is required for embedding eval data loading", file=sys.stderr)
        sys.exit(1)

    print("\n🔍 Loading Embedding Evaluation Data (BEIR Format)...")
    print("   Type: Custom evaluation dataset")
    print(f"   Data Directory: {args.data_dir}")
    print(f"   Workload ID: {args.workload_id}")
    print(f"   Client ID: {args.client_id}")
    print()

    load_embedding_eval_data_to_elasticsearch(
        workload_id=args.workload_id,
        client_id=args.client_id,
        data_dir=args.data_dir,
        dry_run=args.dry_run,
    )


def main():
    """Main entry point for unified data loading."""
    parser = argparse.ArgumentParser(
        description="Unified data loader for all Data Flywheel pipelines.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Data Types:
  llm             - Standard LLM training data (supervised fine-tuning)
  dpo             - DPO preference pairs (chosen vs rejected responses)
  embedding       - Embedding triplets (query-positive-negative format)
  embedding-eval  - Embedding evaluation data (BEIR format)

For detailed usage examples, run:
  python src/scripts/load_data.py --examples
""",
    )

    parser.add_argument(
        "--type",
        choices=["llm", "dpo", "embedding", "embedding-eval"],
        required="--examples" not in sys.argv and "--help" not in sys.argv,
        help="Type of data to load: llm, dpo, embedding, or embedding-eval",
    )

    parser.add_argument(
        "--workload-id",
        help="Unique identifier for the workload (required for DPO, embedding, and embedding-eval)",
    )

    parser.add_argument(
        "--client-id",
        default="",
        help="Unique identifier for the client (required for embedding and embedding-eval, optional for others)",
    )

    parser.add_argument(
        "--file",
        help="Input file path (JSON/JSONL/CSV format). Relative paths are resolved from the data/ directory. "
        "Absolute paths are also supported.",
    )

    parser.add_argument(
        "--data-dir",
        help="Data directory path (for embedding-eval BEIR format datasets)",
    )

    parser.add_argument(
        "--dataset-type",
        choices=["triplet", "bug_pairs", "bug_pairs_separated"],
        default="triplet",
        help="Type of embedding dataset (embedding only).\n"
        "triplet: JSONL/JSON format with [query, pos_doc, neg_doc] or {query, pos_doc, neg_doc}\n"
        "bug_pairs: Single file with bugid, similar_bug, bugdata, similarbug_data columns\n"
        "bug_pairs_separated: Two files - corpus file (--file) and mappings file (--mappings-file)",
    )

    parser.add_argument(
        "--mappings-file",
        help="Mappings file path (required for bug_pairs_separated embedding data)",
    )

    parser.add_argument(
        "--index-name",
        help="Custom Elasticsearch index name (optional, uses defaults based on data type)",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Optional positive integer limit on number of records to load "
            "(default: unlimited - loads all records). Applies to DPO and embedding data types only."
        ),
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1000,
        help="Chunk size for bulk indexing (embedding only, default: 1000)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate loading without actually indexing data (embedding-eval only)",
    )

    parser.add_argument(
        "--examples",
        action="store_true",
        help="Show usage examples and exit",
    )

    args = parser.parse_args()

    # Show examples if requested
    if args.examples:
        print_usage_examples()
        sys.exit(0)

    # Validate required arguments based on data type
    if args.type and args.type != "embedding-eval" and not args.file:
        print(f"Error: --file is required for {args.type} data loading", file=sys.stderr)
        sys.exit(1)

    if args.type == "embedding-eval" and not args.data_dir:
        print("Error: --data-dir is required for embedding-eval data loading", file=sys.stderr)
        sys.exit(1)

    # Set default index names if not provided
    if not args.index_name:
        if args.type == "llm":
            from lib.integration.es_client import ES_COLLECTION_NAME

            args.index_name = ES_COLLECTION_NAME
        elif args.type == "dpo":
            from lib.integration.es_client import ES_DPO_DATA_COLLECTION_NAME

            args.index_name = ES_DPO_DATA_COLLECTION_NAME
        elif args.type == "embedding":
            from lib.integration.es_client import ES_EMBEDDINGS_DATA_COLLECTION_NAME

            args.index_name = ES_EMBEDDINGS_DATA_COLLECTION_NAME

    # Route to appropriate loader
    try:
        if args.type == "llm":
            load_llm_data(args)
        elif args.type == "dpo":
            load_dpo_data(args)
        elif args.type == "embedding":
            load_embedding_data(args)
        elif args.type == "embedding-eval":
            load_embedding_eval_data(args)
    except Exception as e:
        print(f"\n❌ Error loading {args.type} data: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
