#!/usr/bin/env python3
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

"""Script to load custom-eval-dataset into Elasticsearch for embedding evaluation."""

import argparse
import csv
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

from elasticsearch.helpers import bulk, scan  # type: ignore[attr-defined] # noqa: E402
from lib.integration.es_client import (  # noqa: E402
    ES_EMBEDDINGS_DATA_COLLECTION_NAME,
    ES_EMBEDDINGS_SIMILAR_DATA_COLLECTION_NAME,
    get_es_client,
)

from src.scripts.utils import validate_path  # noqa: E402


def poll_existing_data(es, workload_id: str, client_id: str) -> tuple[dict, set]:
    """
    Poll both indexes and return existing data as dictionaries.

    Returns:
        - existing_data: Dict mapping query_id to document data
        - existing_mappings: Set of (query_id, pos_doc_id) tuples
    """
    # Poll main embeddings data index
    existing_data = {}
    try:
        search_query = {
            "query": {
                "bool": {
                    "must": [
                        {"term": {"workload_id": workload_id}},
                        {"term": {"client_id": client_id}},
                    ]
                }
            }
        }

        for hit in scan(
            client=es,
            query=search_query,
            index=ES_EMBEDDINGS_DATA_COLLECTION_NAME,
            size=5000,
            scroll="5m",
        ):
            source = hit["_source"]
            query_id = source.get("query_id")
            if query_id:
                existing_data[query_id] = source

        print(
            f"Found {len(existing_data)} existing records in {ES_EMBEDDINGS_DATA_COLLECTION_NAME}"
        )

    except Exception as e:
        print(
            f"Warning: Could not poll existing data from {ES_EMBEDDINGS_DATA_COLLECTION_NAME}: {e}"
        )

    # Poll similar data mappings index
    existing_mappings = set()
    try:
        search_query = {
            "query": {
                "bool": {
                    "must": [
                        {"term": {"workload_id": workload_id}},
                        {"term": {"client_id": client_id}},
                    ]
                }
            }
        }

        for hit in scan(
            client=es,
            query=search_query,
            index=ES_EMBEDDINGS_SIMILAR_DATA_COLLECTION_NAME,
            size=1000,
            scroll="5m",
        ):
            source = hit["_source"]
            query_id = source.get("query_id")
            pos_doc_id = source.get("pos_doc_id")
            if query_id and pos_doc_id:
                existing_mappings.add((query_id, pos_doc_id))

        print(
            f"Found {len(existing_mappings)} existing mappings in {ES_EMBEDDINGS_SIMILAR_DATA_COLLECTION_NAME}"
        )

    except Exception as e:
        print(
            f"Warning: Could not poll existing mappings from {ES_EMBEDDINGS_SIMILAR_DATA_COLLECTION_NAME}: {e}"
        )

    return existing_data, existing_mappings


def load_corpus_data(
    corpus_file: str,
    workload_id: str,
    client_id: str,
    existing_data: dict,
) -> tuple[list, int, int]:
    """
    Load corpus.jsonl file and prepare bulk actions for indexing.

    Args:
        corpus_file: Path to corpus.jsonl file
        workload_id: Workload identifier
        client_id: Client identifier
        existing_data: Dictionary of existing data to check for duplicates

    Returns:
        - bulk_actions: List of documents to index
        - successful: Count of documents prepared for indexing
        - skipped: Count of documents skipped (already exist)
    """
    bulk_actions = []
    successful = 0
    skipped = 0
    timestamp = int(datetime.utcnow().timestamp())

    print(f"Loading corpus from {corpus_file}...")

    with open(corpus_file, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            if not line.strip():
                continue

            try:
                item = json.loads(line)
                doc_id = str(item.get("_id", ""))
                text = item.get("text", "")

                if not doc_id or not text:
                    print(f"Warning: Line {line_num} missing _id or text, skipping")
                    continue

                # Check if document already exists
                if doc_id in existing_data:
                    skipped += 1
                    continue

                # Create document for indexing
                indexed_doc = {
                    "workload_id": workload_id,
                    "client_id": client_id,
                    "timestamp": timestamp,
                    "query_id": doc_id,
                    "query": text,
                }

                bulk_actions.append(
                    {"_index": ES_EMBEDDINGS_DATA_COLLECTION_NAME, "_source": indexed_doc}
                )

                # Update existing_data to avoid duplicates within this run
                existing_data[doc_id] = indexed_doc
                successful += 1

            except json.JSONDecodeError as e:
                print(f"Error parsing JSON at line {line_num}: {e}")
                continue
            except Exception as e:
                print(f"Unexpected error processing line {line_num}: {e}")
                continue

    print(f"Corpus processing complete: {successful} new, {skipped} skipped")
    return bulk_actions, successful, skipped


def load_queries_data(
    queries_file: str,
    workload_id: str,
    client_id: str,
    existing_data: dict,
) -> tuple[list, int, int]:
    """
    Load queries.jsonl file and prepare bulk actions for indexing.

    Args:
        queries_file: Path to queries.jsonl file
        workload_id: Workload identifier
        client_id: Client identifier
        existing_data: Dictionary of existing data to check for duplicates

    Returns:
        - bulk_actions: List of documents to index
        - successful: Count of documents prepared for indexing
        - skipped: Count of documents skipped (already exist)
    """
    bulk_actions = []
    successful = 0
    skipped = 0
    timestamp = int(datetime.utcnow().timestamp())

    print(f"Loading queries from {queries_file}...")

    with open(queries_file, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            if not line.strip():
                continue

            try:
                item = json.loads(line)
                query_id = str(item.get("_id", ""))
                text = item.get("text", "")

                if not query_id or not text:
                    print(f"Warning: Line {line_num} missing _id or text, skipping")
                    continue

                # Check if query already exists
                if query_id in existing_data:
                    skipped += 1
                    continue

                # Create document for indexing
                indexed_doc = {
                    "workload_id": workload_id,
                    "client_id": client_id,
                    "timestamp": timestamp,
                    "query_id": query_id,
                    "query": text,
                }

                bulk_actions.append(
                    {"_index": ES_EMBEDDINGS_DATA_COLLECTION_NAME, "_source": indexed_doc}
                )

                # Update existing_data to avoid duplicates within this run
                existing_data[query_id] = indexed_doc
                successful += 1

            except json.JSONDecodeError as e:
                print(f"Error parsing JSON at line {line_num}: {e}")
                continue
            except Exception as e:
                print(f"Unexpected error processing line {line_num}: {e}")
                continue

    print(f"Queries processing complete: {successful} new, {skipped} skipped")
    return bulk_actions, successful, skipped


def load_qrels_data(
    qrels_file: str,
    workload_id: str,
    client_id: str,
    existing_mappings: set,
) -> tuple[list, int, int]:
    """
    Load qrels/test.tsv file and prepare similarity mappings for indexing.

    Args:
        qrels_file: Path to test.tsv file
        workload_id: Workload identifier
        client_id: Client identifier
        existing_mappings: Set of existing (query_id, pos_doc_id) mappings

    Returns:
        - new_mappings: List of mapping documents to index
        - successful: Count of mappings prepared for indexing
        - skipped: Count of mappings skipped (already exist)
    """
    new_mappings = []
    successful = 0
    skipped = 0
    timestamp = int(datetime.utcnow().timestamp())

    print(f"Loading qrels from {qrels_file}...")

    with open(qrels_file, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")

        for row_num, row in enumerate(reader, 1):
            try:
                # The TSV file has columns: query-id, corpus-id, score
                query_id = str(row.get("query-id", ""))
                corpus_id = str(row.get("corpus-id", ""))

                if not query_id or not corpus_id:
                    print(f"Warning: Row {row_num} missing query-id or corpus-id, skipping")
                    continue

                # Check if mapping already exists
                if (query_id, corpus_id) in existing_mappings:
                    skipped += 1
                    continue

                # Create mapping document for similarity index
                mapping_doc = {
                    "workload_id": workload_id,
                    "client_id": client_id,
                    "timestamp": timestamp,
                    "query_id": query_id,
                    "pos_doc_id": corpus_id,
                }

                new_mappings.append(mapping_doc)

                # Update existing_mappings to avoid duplicates within this run
                existing_mappings.add((query_id, corpus_id))
                successful += 1

            except Exception as e:
                print(f"Error processing row {row_num}: {e}")
                continue

    print(f"Qrels processing complete: {successful} new, {skipped} skipped")
    return new_mappings, successful, skipped


def bulk_index_documents(es, bulk_actions: list, index_name: str) -> None:
    """Execute bulk indexing for documents."""
    if not bulk_actions:
        return

    print(f"Bulk indexing {len(bulk_actions)} documents to {index_name}...")
    try:
        success_count, failed_items = bulk(
            es,
            bulk_actions,
            index=index_name,
            chunk_size=1000,
            request_timeout=60,
            refresh="wait_for",
        )
        print(f"Successfully bulk indexed {success_count} documents")
        if failed_items:
            print(f"Failed to index {len(failed_items)} documents")
            for item in failed_items[:5]:  # Show first 5 failures
                print(f"  Failed item: {item}")
    except Exception as e:
        print(f"Error during bulk indexing: {e}")
        raise


def bulk_index_mappings(es, new_mappings: list) -> None:
    """Execute bulk indexing for similarity mappings."""
    if not new_mappings:
        return

    print(
        f"Bulk indexing {len(new_mappings)} new mappings to {ES_EMBEDDINGS_SIMILAR_DATA_COLLECTION_NAME}..."
    )
    mapping_actions = [
        {"_index": ES_EMBEDDINGS_SIMILAR_DATA_COLLECTION_NAME, "_source": mapping}
        for mapping in new_mappings
    ]
    try:
        success_count, failed_items = bulk(
            es,
            mapping_actions,
            index=ES_EMBEDDINGS_SIMILAR_DATA_COLLECTION_NAME,
            chunk_size=1000,
            request_timeout=60,
            refresh="wait_for",
        )
        print(f"Successfully bulk indexed {success_count} mapping documents")
        if failed_items:
            print(f"Failed to index {len(failed_items)} mapping documents")
            for item in failed_items[:5]:  # Show first 5 failures
                print(f"  Failed item: {item}")
    except Exception as e:
        print(f"Error during bulk indexing mappings: {e}")
        raise


def load_embedding_eval_data_to_elasticsearch(
    workload_id: str,
    client_id: str,
    data_dir: str = "custom-eval-dataset",
    dry_run: bool = False,
):
    """
    Main function to load embedding eval data into Elasticsearch.

    Args:
        workload_id: Workload identifier
        client_id: Client identifier
        data_dir: Directory containing the dataset files
        dry_run: If True, only simulate the loading without indexing
    """
    if not workload_id:
        raise ValueError("workload_id is required")

    # Construct file paths
    data_path = Path(data_dir)
    corpus_file = data_path / "corpus.jsonl"
    queries_file = data_path / "queries.jsonl"
    qrels_file = data_path / "qrels" / "test.tsv"

    # Validate and resolve file paths
    validated_paths = {}
    for file_path, file_name in [
        (corpus_file, "corpus.jsonl"),
        (queries_file, "queries.jsonl"),
        (qrels_file, "qrels/test.tsv"),
    ]:
        # Validate the file path using validate_path with data directory
        validated_path = validate_path(str(file_path), is_input=True, data_dir="data")
        validated_paths[file_name] = validated_path
        print(f"✓ Found {file_name} at: {validated_path}")

    print("-" * 50)
    print(f"Workload ID: {workload_id}")
    print(f"Client ID: {client_id}")
    print(f"Dry run: {dry_run}")
    print("-" * 50)

    # Get Elasticsearch client
    es = get_es_client()

    # Step 1: Pull existing data for workload_id and client_id
    print("\nStep 1: Polling existing data...")
    existing_data, existing_mappings = poll_existing_data(es, workload_id, client_id)
    print("--------\n")

    # Step 2: Process corpus.jsonl
    print("Step 2: Processing corpus.jsonl...")
    corpus_bulk_actions, corpus_success, corpus_skipped = load_corpus_data(
        validated_paths["corpus.jsonl"], workload_id, client_id, existing_data
    )
    print("--------\n")

    # Step 3: Process queries.jsonl
    print("Step 3: Processing queries.jsonl...")
    queries_bulk_actions, queries_success, queries_skipped = load_queries_data(
        validated_paths["queries.jsonl"], workload_id, client_id, existing_data
    )
    print("--------\n")

    # Step 4: Process qrels/test.tsv
    print("Step 4: Processing qrels/test.tsv...")
    new_mappings, qrels_success, qrels_skipped = load_qrels_data(
        validated_paths["qrels/test.tsv"], workload_id, client_id, existing_mappings
    )
    print("--------\n")

    # Combine all bulk actions
    all_bulk_actions = corpus_bulk_actions + queries_bulk_actions

    # Summary
    print("Step 5: Final Summary...")
    print("=" * 50)
    print(f"Corpus documents: {corpus_success} new, {corpus_skipped} skipped")
    print(f"Query documents: {queries_success} new, {queries_skipped} skipped")
    print(f"Qrel mappings: {qrels_success} new, {qrels_skipped} skipped")
    print(f"Total documents to index: {len(all_bulk_actions)}")
    print(f"Total mappings to index: {len(new_mappings)}")
    print("=" * 50)
    print("--------\n")

    if dry_run:
        print("\nDRY RUN MODE - No data will be indexed")
        print("\nSample documents to be indexed:")
        for i, action in enumerate(all_bulk_actions[:3]):
            print(f"\nDocument {i + 1}:")
            print(f"  query_id: {action['_source']['query_id']}")
            print(f"  query (truncated): {action['_source']['query'][:100]}...")

        if new_mappings:
            print("\nSample mappings to be indexed:")
            for i, mapping in enumerate(new_mappings[:3]):
                print(f"\nMapping {i + 1}:")
                print(f"  query_id: {mapping['query_id']}")
                print(f"  pos_doc_id: {mapping['pos_doc_id']}")
                print(f"  score: {mapping.get('score', 1.0)}")
        print("--------\n")
    else:
        # Execute bulk indexing
        print("Step 6: Indexing data...")

        # Index documents
        if all_bulk_actions:
            bulk_index_documents(es, all_bulk_actions, ES_EMBEDDINGS_DATA_COLLECTION_NAME)

        # Index mappings
        if new_mappings:
            bulk_index_mappings(es, new_mappings)

        # Final refresh
        if all_bulk_actions:
            es.indices.refresh(index=ES_EMBEDDINGS_DATA_COLLECTION_NAME)
        if new_mappings:
            es.indices.refresh(index=ES_EMBEDDINGS_SIMILAR_DATA_COLLECTION_NAME)
        print("--------\n")

        print("\nIndexing complete!")


def main():
    """Main entry point for the script."""
    parser = argparse.ArgumentParser(
        description="Load embedding eval data into Elasticsearch for embedding evaluation."
    )
    parser.add_argument(
        "--workload-id",
        required=True,
        help="Unique identifier for the workload (required)",
    )
    parser.add_argument(
        "--client-id",
        required=True,
        help="Unique identifier for the client (required)",
    )
    parser.add_argument(
        "--data-dir",
        default="custom-eval-dataset",
        help="Directory containing the dataset files (default: custom-eval-dataset)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the loading without actually indexing data",
    )

    args = parser.parse_args()

    try:
        load_embedding_eval_data_to_elasticsearch(
            workload_id=args.workload_id,
            client_id=args.client_id,
            data_dir=args.data_dir,
            dry_run=args.dry_run,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
