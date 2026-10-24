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
import hashlib
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

from src.scripts.utils import read_data_file, resolve_file_path  # noqa: E402


def poll_existing_data(es, workload_id: str, client_id: str):
    """Poll both indexes and return existing data as dictionaries."""
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
            f"✓ Found {len(existing_data)} existing records in {ES_EMBEDDINGS_DATA_COLLECTION_NAME}"
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
            existing_mappings.add((hit["_source"]["query_id"], hit["_source"]["pos_doc_id"]))
            existing_mappings.add((hit["_source"]["pos_doc_id"], hit["_source"]["query_id"]))
        print(
            f"✓ Found {len(existing_mappings)} existing mappings in {ES_EMBEDDINGS_SIMILAR_DATA_COLLECTION_NAME}"
        )
    except Exception as e:
        print(
            f"Warning: Could not poll existing mappings from {ES_EMBEDDINGS_SIMILAR_DATA_COLLECTION_NAME}: {e}"
        )
    return existing_data, existing_mappings


def detect_negative_samples(data, sample_size=5):
    """
    Check if negative samples are present by examining the first few rows.
    """
    if not data:
        print("Warning: Empty data provided to detect_negative_samples")
        return False

    sample_size = min(sample_size, len(data))
    sample_data = data[:sample_size]

    has_negatives = True
    for item in sample_data:
        if isinstance(item, list) and len(item) >= 3:
            if len(item) < 3 or not item[2]:
                has_negatives = False
                break
        elif isinstance(item, dict):
            neg_doc = item.get("neg_doc", "")
            if not neg_doc or (isinstance(neg_doc, list) and not neg_doc):
                has_negatives = False
                break
        else:
            has_negatives = False
            break

    print(
        f"Negative samples {'detected' if has_negatives else 'not detected'} in sample of {sample_size} records"
    )
    return has_negatives


def extract_timestamp(bug_data):
    """Extract timestamp from bug data by parsing the requestdate field."""
    from datetime import datetime

    from dateutil import parser

    try:
        parts = str(bug_data).split("|")
        for part in parts:
            part = part.strip()
            # Case insensitive check
            if part.lower().startswith("requestdate:"):
                # Split by FIRST colon
                date_str = part.split(":", 1)[1].strip()
                try:
                    dt = parser.parse(date_str)
                    return int(dt.timestamp())
                except Exception as e:
                    print(f"Warning: Failed to parse date '{date_str}': {e}")
                    pass

        return int(datetime.utcnow().timestamp())
    except Exception:
        return int(datetime.utcnow().timestamp())


def generate_content_based_id(text: str) -> str:
    """Generate a deterministic ID based on content hash."""
    normalized = " ".join(text.strip().lower().split())
    content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return content_hash


def process_single_format(
    data: str, workload_id: str, client_id: str, index_name: str, existing_data: dict
) -> tuple[dict, str]:
    """Process single format data."""
    timestamp = int(datetime.utcnow().timestamp())
    doc_id = generate_content_based_id(data)
    indexed_doc = {
        "workload_id": workload_id,
        "client_id": client_id,
        "timestamp": timestamp,
        "query": data,
        "query_id": doc_id,
    }
    return {"_index": index_name, "_source": indexed_doc}, doc_id


def should_skip_bug_record(bugdata: str, similarbug_data: str) -> bool:
    """Check if bug record should be skipped."""
    return bugdata.startswith("[Missing doc for bugid:") or similarbug_data.startswith(
        "[Missing doc for bugid:"
    )


def create_bug_document(
    bug_id: str,
    bug_data: str,
    workload_id: str,
    client_id: str,
    has_negative_samples: bool,
    item: dict | None,
) -> dict:
    """Create an indexed document for bug data."""
    bug_timestamp = extract_timestamp(bug_data)
    neg_doc = []
    if has_negative_samples and item is not None:
        neg_doc = item.get("neg_doc", [])

    return {
        "workload_id": workload_id,
        "client_id": client_id,
        "timestamp": bug_timestamp,
        "query_id": bug_id,
        "query": bug_data,
        "neg_doc": neg_doc,
    }


def add_document_to_bulk(
    doc_id: str,
    doc_data: str,
    workload_id: str,
    client_id: str,
    index_name: str,
    existing_data: dict,
    bulk_actions: list,
    has_negative_samples: bool = False,
    item: dict | None = None,
    is_bug_doc: bool = False,
) -> bool:
    """Helper to add a document to bulk actions if it doesn't exist."""
    if doc_id not in existing_data:
        if is_bug_doc:
            indexed_doc = create_bug_document(
                doc_id, doc_data, workload_id, client_id, has_negative_samples, item
            )
            # For bug docs, we need to return the dict for consistency with existing code
            # But here we'll standardize on ES bulk format
            source = indexed_doc
        else:
            # Reusing process_single_format logic partially
            timestamp = int(datetime.utcnow().timestamp())
            source = {
                "workload_id": workload_id,
                "client_id": client_id,
                "timestamp": timestamp,
                "query": doc_data,
                "query_id": doc_id,
            }

        bulk_actions.append({"_index": index_name, "_source": source})
        existing_data[doc_id] = source
        return True
    return False


def add_mapping(
    query_id: str,
    pos_doc_id: str,
    workload_id: str,
    client_id: str,
    existing_mappings: set,
    new_mappings: list,
    neg_doc_ids: list | None = None,
    timestamp: int | None = None,
):
    """Helper to add a mapping if it doesn't exist."""
    if (query_id, pos_doc_id) not in existing_mappings:
        mapping_doc = {
            "timestamp": timestamp or int(datetime.utcnow().timestamp()),
            "query_id": query_id,
            "pos_doc_id": pos_doc_id,
            "workload_id": workload_id,
            "client_id": client_id,
        }
        if neg_doc_ids:
            mapping_doc["neg_doc_ids"] = neg_doc_ids

        new_mappings.append(mapping_doc)
        existing_mappings.add((query_id, pos_doc_id))
        existing_mappings.add((pos_doc_id, query_id))
        return True
    return False


def process_bug_format(
    item: dict,
    workload_id: str,
    client_id: str,
    index_name: str,
    has_negative_samples: bool,
    existing_data: dict,
    existing_mappings: set,
) -> tuple[list, list, int, int]:
    """Process bug data format."""
    bugdata = str(item["bugdata"])
    similarbug_data = str(item["similarbug_data"])

    if should_skip_bug_record(bugdata, similarbug_data):
        return [], [], 0, 1

    query_id = str(item["bugid"])
    pos_doc_id = str(item["similar_bug"])
    bulk_actions = []
    new_mappings = []
    successful = 0

    # Process query document
    if add_document_to_bulk(
        query_id,
        bugdata,
        workload_id,
        client_id,
        index_name,
        existing_data,
        bulk_actions,
        has_negative_samples,
        item,
        is_bug_doc=True,
    ):
        successful += 1

    # Process positive document
    if add_document_to_bulk(
        pos_doc_id,
        similarbug_data,
        workload_id,
        client_id,
        index_name,
        existing_data,
        bulk_actions,
        has_negative_samples,
        item,
        is_bug_doc=True,
    ):
        successful += 1

    # Create mapping
    mapping_timestamp = extract_timestamp(bugdata)
    add_mapping(
        query_id,
        pos_doc_id,
        workload_id,
        client_id,
        existing_mappings,
        new_mappings,
        timestamp=mapping_timestamp,
    )

    return bulk_actions, new_mappings, successful, 0


def bulk_index_documents(es, bulk_actions: list, index_name: str, chunk_size: int = 1000) -> None:
    """Execute bulk indexing for documents."""
    if not bulk_actions:
        return

    print(f"Bulk indexing {len(bulk_actions)} documents to {index_name}...")
    try:
        success_count, failed_items = bulk(
            es,
            bulk_actions,
            index=index_name,
            chunk_size=chunk_size,
            request_timeout=60,
            refresh="wait_for",
        )
        print(f"Successfully bulk indexed {success_count} documents")
        if failed_items:
            print(f"Failed to index {len(failed_items)} documents")
    except Exception as e:
        print(f"Error during bulk indexing: {e}")


def bulk_index_mappings(es, new_mappings: list, chunk_size: int = 1000) -> None:
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
            chunk_size=chunk_size,
            request_timeout=60,
            refresh="wait_for",
        )
        print(f"Successfully bulk indexed {success_count} mapping documents")
        if failed_items:
            print(f"Failed to index {len(failed_items)} mapping documents")
    except Exception as e:
        print(f"Error during bulk indexing mappings: {e}")


def format_row_to_string(row: dict) -> str:
    """Converts a DataFrame row into the desired '|' separated string format."""
    parts = []
    for column_name, value in row.items():
        safe_value = str(value) if value is not None else ""
        part = f"{column_name}:{safe_value}"
        parts.append(part)
    return " | ".join(parts)


def process_query_pos_neg_format(
    query,
    pos_doc,
    neg_docs,
    workload_id,
    client_id,
    index_name,
    existing_data,
    bulk_actions,
    new_mappings,
    existing_mappings,
):
    """
    Process query, positive doc, and negative docs format.
    Common logic for both list and dict formats.
    """
    # Process query
    query_doc, query_id = process_single_format(
        query, workload_id, client_id, index_name, existing_data
    )
    if query_id not in existing_data:
        bulk_actions.append(query_doc)
        existing_data[query_id] = query_doc["_source"]

    # Process positive document
    pos_doc_doc, pos_doc_id = process_single_format(
        pos_doc, workload_id, client_id, index_name, existing_data
    )
    if pos_doc_id not in existing_data:
        bulk_actions.append(pos_doc_doc)
        existing_data[pos_doc_id] = pos_doc_doc["_source"]

    # Process negative documents
    neg_doc_ids = []
    if not isinstance(neg_docs, list):
        neg_docs = [neg_docs]

    for neg_doc in neg_docs:
        neg_doc_doc, neg_doc_id = process_single_format(
            neg_doc, workload_id, client_id, index_name, existing_data
        )
        if neg_doc_id not in existing_data:
            bulk_actions.append(neg_doc_doc)
            existing_data[neg_doc_id] = neg_doc_doc["_source"]
        neg_doc_ids.append(neg_doc_id)

    # Create mapping document using helper
    add_mapping(
        query_id,
        pos_doc_id,
        workload_id,
        client_id,
        existing_mappings,
        new_mappings,
        neg_doc_ids=neg_doc_ids,
    )
    return 1


def process_mappings_file(
    mappings_data: list,
    workload_id: str,
    client_id: str,
    existing_mappings: set,
    new_mappings: list,
):
    """Process mappings file for bug_pairs_separated format."""
    for item in mappings_data:
        try:
            query_id = str(item.get("bugid", ""))
            duplicates = item.get("Duplicates", "")

            pos_doc_ids = [int(float(part.strip())) for part in duplicates.split(",")]

            if not query_id or not pos_doc_ids:
                continue

            for pos_doc_id in pos_doc_ids:
                add_mapping(
                    query_id,
                    str(pos_doc_id),
                    workload_id,
                    client_id,
                    existing_mappings,
                    new_mappings,
                )
        except (ValueError, KeyError) as e:
            print(f"Data validation error for record: {e}")
        except Exception as e:
            print(f"Unexpected error processing record: {e}")


def load_embedding_data_to_elasticsearch(
    workload_id: str,
    client_id: str = "",
    file_path: str | Path = "embedding_data.jsonl",
    dataset_type: str = "triplet",
    mappings_file_path: str | Path | None = None,
    index_name: str = ES_EMBEDDINGS_DATA_COLLECTION_NAME,
    limit: int | None = None,
    chunk_size: int = 1000,
):
    """Load embedding data from JSON/JSONL/CSV file into Elasticsearch."""
    if not workload_id:
        raise ValueError("workload_id is required")

    # Validate separated files
    if dataset_type == "bug_pairs_separated" and not mappings_file_path:
        raise ValueError("mappings_file_path is required for bug_pairs_separated dataset type")

    es = get_es_client()

    print("-" * 50)
    print(f"Workload ID: {workload_id}")
    print(f"Client ID: {client_id}")
    print(f"Dataset Type: {dataset_type}")
    print("-" * 50)

    # Resolve and read file
    print("\nStep 1: Loading data...")
    safe_path = resolve_file_path(file_path)
    print(f"Loading corpus from {safe_path}...")
    data = read_data_file(safe_path, limit)
    file_extension = safe_path.lower().split(".")[-1]
    print(f"Processing {len(data)} records from {file_extension.upper()} file...")

    mappings_data = []
    if dataset_type == "bug_pairs_separated" and mappings_file_path:
        safe_mappings_path = resolve_file_path(mappings_file_path)
        print(f"Loading mappings from {safe_mappings_path}...")
        mappings_data = read_data_file(safe_mappings_path)
        print(f"Read {len(mappings_data)} mappings from {safe_mappings_path}")
    print("--------\n")

    # Poll existing data
    print("Step 2: Polling existing data...")
    existing_data, existing_mappings = poll_existing_data(es, workload_id, client_id)
    print(
        f"Found {len(existing_data)} existing records and {len(existing_mappings)} existing mappings"
    )
    print("--------\n")

    # Detect negative samples
    print("Step 3: Validating data...")
    has_negative_samples = detect_negative_samples(data)
    print(f"Negative samples detected: {has_negative_samples}")
    print("--------\n")

    # Process records
    print("Step 4: Processing records...")
    successful = 0
    skipped = 0
    bulk_actions = []
    new_mappings = []

    for item in data:
        try:
            if dataset_type == "triplet":
                if isinstance(item, list) and len(item) >= 2:
                    query = item[0]
                    pos_doc = item[1]
                    neg_docs = item[2] if len(item) > 2 else []
                    success_count = process_query_pos_neg_format(
                        query,
                        pos_doc,
                        neg_docs,
                        workload_id,
                        client_id,
                        index_name,
                        existing_data,
                        bulk_actions,
                        new_mappings,
                        existing_mappings,
                    )
                    successful += success_count
                elif isinstance(item, dict) and "query" in item and "pos_doc" in item:
                    neg_doc = item.get("neg_doc", [])
                    success_count = process_query_pos_neg_format(
                        item["query"],
                        item["pos_doc"],
                        neg_doc,
                        workload_id,
                        client_id,
                        index_name,
                        existing_data,
                        bulk_actions,
                        new_mappings,
                        existing_mappings,
                    )
                    successful += success_count

            elif dataset_type == "bug_pairs":
                if isinstance(item, dict) and all(
                    k in item for k in ["bugid", "similar_bug", "bugdata", "similarbug_data"]
                ):
                    actions, mappings, success_count, skip_count = process_bug_format(
                        item,
                        workload_id,
                        client_id,
                        index_name,
                        has_negative_samples,
                        existing_data,
                        existing_mappings,
                    )
                    bulk_actions.extend(actions)
                    new_mappings.extend(mappings)
                    successful += success_count
                    skipped += skip_count

            elif dataset_type == "bug_pairs_separated":
                if isinstance(item, dict) and "bugid" in item:
                    query_id = str(item["bugid"])
                    query = format_row_to_string(item)
                    if add_document_to_bulk(
                        query_id,
                        query,
                        workload_id,
                        client_id,
                        index_name,
                        existing_data,
                        bulk_actions,
                        has_negative_samples,
                        item,
                        is_bug_doc=True,
                    ):
                        successful += 1

        except (ValueError, KeyError) as e:
            print(f"Data validation error for record: {e}")
        except Exception as e:
            print(f"Unexpected error processing record: {e}")

    if dataset_type == "bug_pairs_separated":
        process_mappings_file(
            mappings_data, workload_id, client_id, existing_mappings, new_mappings
        )

    print(f"Processed {len(data)} records: {successful} indexed, {skipped} skipped")
    if new_mappings:
        print(f"Created {len(new_mappings)} new similarity mappings")
    print("--------\n")

    # Execute bulk operations
    print("Step 5: Indexing data...")
    bulk_index_documents(es, bulk_actions, index_name, chunk_size)
    bulk_index_mappings(es, new_mappings, chunk_size)

    # Final refresh
    es.indices.refresh(index=index_name)
    if new_mappings:
        es.indices.refresh(index=ES_EMBEDDINGS_SIMILAR_DATA_COLLECTION_NAME)
    print("--------\n")

    print(f"Processed {len(data)} records: {successful} indexed, {skipped} skipped")
    if new_mappings:
        print(f"Created {len(new_mappings)} new similarity mappings")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load embedding training data into Elasticsearch.")
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
        "--dataset-type",
        required=True,
        help="Type of dataset to load (required).\n"
        "triplet: Data containing query, positive document, and optional negative documents.\n"
        "bug_pairs: Single file containing bug IDs and their full text content along with similar bug pairs.\n"
        "bug_pairs_separated: Two files - one for corpus (full text) and one for mappings (ID pairs). Requires --mappings-file.",
        choices=["triplet", "bug_pairs", "bug_pairs_separated"],
        default="triplet",
    )
    parser.add_argument(
        "--file",
        default="embedding_data.jsonl",
        help="Input JSON/JSONL/CSV file path. Relative paths are resolved from the data/ directory. "
        "Absolute paths are also supported. For bug_pairs_separated, this is the corpus file. "
        "(default: embedding_data.jsonl)",
    )
    parser.add_argument(
        "--mappings-file",
        help="Input JSON/JSONL/CSV mappings file path. Relative paths are resolved from the data/ directory. "
        "Absolute paths are also supported. (required for bug_pairs_separated)",
    )
    parser.add_argument(
        "--index-name",
        default=ES_EMBEDDINGS_DATA_COLLECTION_NAME,
        help="Elasticsearch index name (default: flywheel_embeddings_data)",
    )
    parser.add_argument("--chunk-size", type=int, default=1000, help="Chunk size for bulk indexing")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional positive integer limit on number of records to load (default: unlimited)",
    )

    args = parser.parse_args()

    try:
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
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
