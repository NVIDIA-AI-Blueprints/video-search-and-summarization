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

import json
import random
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np
from bson import ObjectId

from src.api.db import get_db
from src.api.models import DatasetType, WorkloadClassification
from src.backends.factory import get_backend_provider
from src.config import DataSplitConfig, EmbeddingDataSplitConfig
from src.lib.flywheel.icl_selection import ICLSelection
from src.lib.flywheel.util import (
    format_embedding_training_data,
    format_evaluator,
    format_training_data,
    split_embedding_records,
    split_records,
    truncate_embedding_records,
)
from src.lib.integration.data_validator import DataValidator
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.dataset_creator")


class DatasetCreator:
    records: list[dict[str, Any]]
    flywheel_run_id: str
    output_dataset_prefix: str
    workload_id: str
    ts: int
    split_config: DataSplitConfig

    def __init__(
        self,
        records: list[dict[str, Any]],
        flywheel_run_id: str,
        output_dataset_prefix: str,
        workload_id: str,
        client_id: str,
        split_config: DataSplitConfig,
        icl_config=None,
    ):
        self.records = records
        self.flywheel_run_id = flywheel_run_id
        self.output_dataset_prefix = output_dataset_prefix
        self.workload_id = workload_id
        self.client_id = client_id
        self.ts = int(datetime.utcnow().timestamp())
        self.split_config = split_config
        self.icl_config = icl_config

    def create_datasets(
        self, workload_type: WorkloadClassification
    ) -> tuple[str | None, dict[str, str]]:
        # Validate and clean records
        validator = DataValidator()
        validated_records = validator.validate_records(
            self.records,
            workload_type,
            split_config=self.split_config,
        )

        logger.info(
            f"Validation completed: {len(validated_records)} valid records from {len(self.records)} original records"
        )

        self.records = validated_records

        # Update FlywheelRun document with number of records
        db = get_db()
        db.flywheel_runs.update_one(
            {"_id": ObjectId(self.flywheel_run_id)}, {"$set": {"num_records": len(self.records)}}
        )

        # split the jsonl data into train and val
        eval_records, train_records, val_records = split_records(self.records, self.split_config)
        logger.info(
            f"Split {len(self.records)} records into {len(eval_records)} eval, {len(train_records)} train, {len(val_records)} val"
        )

        # Keep original records for ICL selection before formatting
        original_train_records = train_records.copy()
        original_eval_records = eval_records.copy()

        ## format the training data
        train_records = format_training_data(train_records, workload_type)
        val_records = format_training_data(val_records, workload_type)

        # Format evaluation data for OpenAI API compatibility (convert tool call args to strings)
        eval_records = format_evaluator(eval_records)

        # Convert all record sets to JSONL format
        eval_jsonl_data, train_jsonl_data, val_jsonl_data = (
            "\n".join(json.dumps(record) for record in records)
            for records in [eval_records, train_records, val_records]
        )

        eval_dataset_name = f"flywheel-eval-{self.output_dataset_prefix + '-' if self.output_dataset_prefix else ''}{self.workload_id}-{self.ts}"
        eval_uploader = get_backend_provider().datastore(dataset_name=eval_dataset_name)
        eval_uploader.upload_data(eval_jsonl_data, "eval_data.jsonl")

        icl_selector = ICLSelection(self.icl_config, self.workload_id, self.client_id)
        index_name, icl_records = icl_selector.generate_icl_records(
            original_train_records, workload_type, original_eval_records
        )
        # Format ICL records for evaluation as well
        icl_records = format_evaluator(icl_records)
        icl_jsonl_data = "\n".join(json.dumps(record) for record in icl_records)
        icl_dataset_name = f"flywheel-icl-{self.output_dataset_prefix + '-' if self.output_dataset_prefix else ''}{self.workload_id}-{self.ts}"
        icl_uploader = get_backend_provider().datastore(dataset_name=icl_dataset_name)
        icl_uploader.upload_data(icl_jsonl_data, "eval_data.jsonl")

        train_dataset_name = f"flywheel-train-{self.output_dataset_prefix + '-' if self.output_dataset_prefix else ''}{self.workload_id}-{self.ts}"
        train_uploader = get_backend_provider().datastore(dataset_name=train_dataset_name)
        train_uploader.upload_data(train_jsonl_data, "training/train_data.jsonl")
        train_uploader.upload_data(val_jsonl_data, "validation/val_data.jsonl")

        # update the flywheel run with the dataset names
        db.flywheel_runs.update_one(
            {"_id": ObjectId(self.flywheel_run_id)},
            {
                "$set": {
                    "datasets": [
                        {
                            "name": eval_dataset_name,
                            "num_records": len(eval_records),
                            "nmp_uri": eval_uploader.get_file_uri(),
                        },
                        {
                            "name": icl_dataset_name,
                            "num_records": len(icl_records),
                            "nmp_uri": icl_uploader.get_file_uri(),
                        },
                        {
                            "name": train_dataset_name,
                            "num_records": len(train_records),
                            "nmp_uri": train_uploader.get_file_uri(),
                        },
                    ],
                }
            },
        )

        return (
            index_name,
            {
                DatasetType.BASE: eval_dataset_name,
                DatasetType.ICL: icl_dataset_name,  # as testing record are converted to icl records and uploaded
                DatasetType.TRAIN: train_dataset_name,
            },
        )


class EmbeddingDatasetCreator(DatasetCreator):
    """Dataset creator for embedding models - expects EmbeddingDataSplitConfig."""

    split_config: EmbeddingDataSplitConfig  # Override type annotation from parent

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def build_relationships(
        self, data: list[dict[str, Any]]
    ) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        """
        Build an undirected graph of document relationships.

        Each document is connected to its similar documents bidirectionally,
        creating an undirected graph where edges represent similarity relationships.

        Args:
            data: List of records containing query_id and pos_doc_id relationships

        Returns:
            Dictionary mapping document IDs to sets of related document IDs
        """
        pos_graph = defaultdict(set)
        neg_graph = defaultdict(set)
        doc_graph = defaultdict(set)

        for _, row in enumerate(data):
            # Extract bug IDs
            docid = row["query_id"]
            similar_docs = row["pos_doc_id"]
            neg_docs = row.get("neg_doc_ids", [])

            doc_graph[docid].add(similar_docs)
            doc_graph[similar_docs].add(docid)
            # Build relationships
            pos_graph[docid].add(similar_docs)

            # add negatives for both the docid and the similar_docs
            for neg_doc in neg_docs:
                neg_graph[docid].add(neg_doc)

        return dict(pos_graph), dict(neg_graph), dict(doc_graph)

    def filter_records(
        self, pos_graph: dict[str, set[str]], neg_graph: dict[str, set[str]], records: list[dict]
    ) -> dict[str, set[str]]:
        """
        Get negative documents from data.
        """
        all_docs = defaultdict(dict)
        all_ids = [record["query_id"] for record in records]
        # from similar records only pick the records in the given date range

        for record in records:
            docid = record["query_id"]
            neg_docs = neg_graph.get(docid, set())
            pos_docs = pos_graph.get(docid, set())

            # filter of ids are in the records
            pos_docs = [id for id in pos_docs if id in all_ids]
            neg_docs = [id for id in neg_docs if id in all_ids]

            # skip the record ig no pos_ids
            if not pos_docs:
                continue

            all_docs[docid] = {
                "pos_docs": list(pos_docs),
                "neg_docs": list(neg_docs),
            }
        return all_docs

    def find_connected_components(self, graph: dict[str, set[str]]) -> list[set[str]]:
        """
        Find all connected components (document clusters) using depth-first search.

        Args:
            graph: Dictionary mapping document IDs to sets of connected document IDs

        Returns:
            List of sets, where each set contains document IDs in one connected component
        """
        if not graph:
            logger.warning("Empty graph provided to find_connected_components")
            return []

        visited = set()
        clusters = []

        def dfs(start: str) -> set[str]:
            """Depth-first search to find all nodes in a component."""
            stack = [start]
            component = set()

            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                component.add(node)

                # Add unvisited neighbors to stack
                for neighbor in graph.get(node, set()):
                    if neighbor not in visited:
                        stack.append(neighbor)

            return component

        # Find all connected components by iterating through all nodes
        for node in graph:
            if node not in visited:
                component = dfs(node)
                if component:  # Only add non-empty components
                    clusters.append(component)

        cluster_sizes = [len(c) for c in clusters]
        logger.info(
            f"Found {len(clusters)} connected components. "
            f"Sizes: min={min(cluster_sizes) if cluster_sizes else 0}, "
            f"max={max(cluster_sizes) if cluster_sizes else 0}, "
            f"avg={sum(cluster_sizes) / len(cluster_sizes) if cluster_sizes else 0:.1f}"
        )
        return clusters

    def build_doc_graph(
        self, similar_records: list[dict]
    ) -> tuple[dict[str, set[str]], dict[str, set[str]], list[set[str]]]:
        """
        Build document graph and find clusters from similarity records.

        This method creates an undirected graph from similarity relationships
        and identifies connected components (clusters of similar documents).

        Args:
            similar_records: List of records with similarity relationships

        Returns:
            Tuple of (document graph, list of clusters)
        """
        logger.info(f"Building document graph from {len(similar_records)} similarity records")
        pos_graph, neg_graph, doc_graph = self.build_relationships(similar_records)
        clusters = self.find_connected_components(doc_graph)
        return pos_graph, neg_graph, clusters

    def curate_negative_samples(
        self,
        all_docs: dict[str, dict[str, set[str]]],
        clusters: list[set[str]],
        min_negatives: int = 5,
        max_negatives: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Curate negative samples for triplet-based embedding training.

        Args:
            all_docs: Dictionary mapping document IDs to sets of similar document IDs and negative document IDs
            clusters: List of document clusters (connected components)
            min_negatives: Minimum number of negative samples per triplet
            max_negatives: Maximum number of negative samples per triplet

        Returns:
            List of triplets with query, positive document, and negative documents
        """
        if min_negatives > max_negatives:
            raise ValueError(
                f"min_negatives ({min_negatives}) cannot be greater than max_negatives ({max_negatives})"
            )

        logger.info(
            f"Curating negative samples with min={min_negatives}, max={max_negatives} "
            f"from {len(clusters)} clusters"
        )

        doc_to_cluster = {}
        cluster_to_docs = {}
        triplets = []

        random.seed(self.split_config.random_seed)
        np.random.seed(self.split_config.random_seed)

        try:
            # Create ID to query mapping
            id_to_query = {
                record["query_id"]: record["query"]
                for record in self.records
                if "query_id" in record
            }
            all_record_ids = np.array(list(id_to_query.keys()))

            for i, cluster in enumerate(clusters):
                cluster_to_docs[i] = set(cluster)  # Use set for faster lookups
                for doc in cluster:
                    doc_to_cluster[doc] = i

            # Track statistics
            skipped_no_cluster = 0
            skipped_no_valid_cluster_docs = 0
            skipped_no_pos_ids = 0
            skipped_insufficient_negatives = 0
            created_triplets = 0
            used_predefined_negatives = 0  # Add this
            used_random_negatives = 0

            # Process each record to create triplets
            for docid, doc_data in all_docs.items():
                if docid not in id_to_query:
                    continue

                cluster_idx = doc_to_cluster.get(docid)
                if cluster_idx is None:
                    skipped_no_cluster += 1
                    continue

                # Get documents in the same cluster (excluding current)
                cluster_docs = list(cluster_to_docs[cluster_idx] - {docid})
                valid_cluster_docs = [id for id in cluster_docs if id in id_to_query]

                if not valid_cluster_docs:
                    skipped_no_valid_cluster_docs += 1
                    continue

                # Get data from the ids
                pos_ids = doc_data["pos_docs"]
                neg_ids = doc_data["neg_docs"]

                if neg_ids:
                    # create triplets for the imcoming bugid and the neg_ids
                    # limit the negatives to max_negatives
                    num_negatives = min(len(neg_ids), max_negatives)
                    negatives = neg_ids[:num_negatives]
                    used_predefined_negatives += 1
                else:
                    # Get valid negative documents (outside the cluster)
                    valid_docs = all_record_ids[
                        ~np.isin(all_record_ids, {*valid_cluster_docs, docid})
                    ]

                    if len(valid_docs) < min_negatives:
                        skipped_insufficient_negatives += 1
                        continue

                    num_negatives = random.randint(
                        min_negatives, min(len(valid_docs), max_negatives)
                    )
                    negatives = np.random.choice(
                        valid_docs, size=num_negatives, replace=False
                    ).tolist()
                    used_random_negatives += 1
                # create triplets for the imcoming bugid and the pos_ids
                for pos_id in pos_ids:
                    triplet = {
                        "query": id_to_query[docid],
                        "query_id": docid,
                        "pos_doc": id_to_query[pos_id],
                        "pos_doc_id": pos_id,
                        "neg_doc": [id_to_query[neg_id] for neg_id in negatives],
                        "neg_doc_ids": [
                            {
                                "id": neg_id,
                                "text": id_to_query[neg_id],
                            }
                            for neg_id in negatives
                        ],
                    }
                    triplets.append(triplet)
                    created_triplets += 1

            # Log statistics
            total_skipped = (
                skipped_no_cluster
                + skipped_no_valid_cluster_docs
                + skipped_no_pos_ids
                + skipped_insufficient_negatives
            )
            logger.info(
                f"Created {created_triplets} triplets from {len(self.records)} records. "
                f"Skipped {total_skipped} records: "
                f"no_cluster={skipped_no_cluster}, "
                f"no_valid_cluster_docs={skipped_no_valid_cluster_docs}, "
                f"no_positive_ids={skipped_no_pos_ids}, "
                f"Used predefined negatives: {used_predefined_negatives}, "
                f"Used random negatives: {used_random_negatives}. "
                f"insufficient_negatives={skipped_insufficient_negatives}"
            )

        except Exception as e:
            logger.error(f"Error curating negative samples: {e}", exc_info=True)
            raise

        return triplets

    def escape_query_clean(self, query: str) -> str:
        """Fix text to be JSON-safe using standard json library."""
        if not query:
            return ""

        # 1. Sanitize: Remove characters you strictly don't want
        cleaned = str(query).replace("\x00", "").replace("`", "")

        # 2. Escape: Use json.dumps to handle ALL standard JSON escaping safely
        escaped = json.dumps(cleaned, ensure_ascii=False)[1:-1]

        # 3. Custom: Handle special template syntax escaping if needed
        return escaped.replace("${", "\\${").replace("}", "\\}")

    def create_eval_corpus(self, eval_records: list[dict]) -> list[dict]:
        """
        Create eval corpus from eval records.
        """
        corpus = []
        all_ids = set()
        for _, record in enumerate(eval_records):
            if record["pos_doc_id"] not in all_ids:
                all_ids.add(record["pos_doc_id"])
                doc_entry = {
                    "_id": str(record["pos_doc_id"]),
                    "title": "",
                    "text": self.escape_query_clean(record["pos_doc"]),
                }
                corpus.append(doc_entry)
            for neg_id in record["neg_doc_ids"]:
                if neg_id["id"] not in all_ids:
                    all_ids.add(neg_id["id"])
                    doc_entry = {
                        "_id": str(neg_id["id"]),
                        "title": "",
                        "text": self.escape_query_clean(neg_id["text"]),
                    }
                    corpus.append(doc_entry)
        return corpus

    def create_eval_queries(
        self, eval_records: list[dict], ids: list[str] | None = None
    ) -> list[dict]:
        """
        Create queries from eval records.
        If ids are provided, only create queries for the ids in the list.
        """
        queries = []
        for _, record in enumerate(eval_records):
            if not ids or (ids and record["query_id"] in ids):
                queries.append(
                    {
                        "_id": str(record["query_id"]),
                        "text": self.escape_query_clean(record["query"]),
                    }
                )
        return queries

    def create_eval_qrels(self, eval_records: list[dict]) -> list[dict]:
        """
        Create qrels from eval records.
        """
        qrels = []

        for _, record in enumerate(eval_records):
            qrels.append(
                {"query-id": record["query_id"], "corpus-id": record["pos_doc_id"], "score": 1}
            )
        return qrels

    def process_custom_eval_dataset(self, eval_datasets: dict) -> dict:
        custom_eval_datasets = []
        dataset_name_map = {}
        for eval_data in eval_datasets:
            name = eval_data["name"]
            eval_records = eval_data["records"]
            similar_records = eval_data["similar_records"]
            workload_id = eval_data["workload_id"]

            # Prepare evaluation records with negative samples
            # filter queries from the corpus
            ids = [record["query_id"] for record in similar_records]

            eval_records = truncate_embedding_records(eval_records)
            corpus = []
            for record in eval_records:
                if record["query_id"] not in ids:
                    corpus.append(
                        {
                            "_id": str(record["query_id"]),
                            "title": "",
                            "text": record["query"],
                        }
                    )

            logger.info(f"Filtering {len(ids)} queries from {len(eval_records)} eval records")
            queries = self.create_eval_queries(eval_records, ids)
            eval_queries_data = "\n".join(json.dumps(record) for record in queries)
            corpus_data = "\n".join(json.dumps(record) for record in corpus)
            logger.info(f"Created {len(corpus)} corpus records")
            logger.info(f"Created {len(queries)} queries")

            qrels = self.create_eval_qrels(similar_records)
            # Upload custom evaluation dataset
            dataset_name = f"flywheel-eval-{name}-{workload_id}-{self.ts}"
            uploader = get_backend_provider().datastore(dataset_name=dataset_name)
            uploader.upload_data(corpus_data, "corpus.jsonl")
            uploader.upload_data(eval_queries_data, "queries.jsonl")
            uploader.upload_tsv_file(qrels, "qrels/test.tsv")

            logger.info(
                f"Created custom evaluation dataset: {dataset_name} ({len(queries)} records)"
            )
            custom_eval_datasets.append(
                {
                    "name": dataset_name,
                    "num_records": len(queries),
                    "nmp_uri": uploader.get_file_uri(),
                }
            )
            dataset_name_map[name] = dataset_name
        return custom_eval_datasets, dataset_name_map

    def create_embedding_datasets(
        self, similar_records: list[dict] | None = None, eval_datasets: list[dict] | None = None
    ) -> dict[str, str]:
        """
        Create datasets for embedding fine-tuning.

        Args:
            similar_records: List of similarity relationships
            eval_datasets: Optional list of custom evaluation datasets with records and similar_records

        Returns:
            Dictionary mapping DatasetType to dataset names
        """
        if not self.records:
            raise ValueError(f"No records found for workload {self.workload_id}")

        # Check if we need to curate negative samples
        if similar_records is None:
            raise ValueError("similar_records required.")

        # Truncate records to prevent exceeding character limits (65535 chars)
        logger.info("Applying truncation to records to prevent exceeding character limits")
        self.records = truncate_embedding_records(self.records)

        logger.info("Curating negative samples from similarity data")
        pos_graph, neg_graph, clusters = self.build_doc_graph(similar_records)
        all_docs = self.filter_records(pos_graph, neg_graph, self.records)

        # Use min/max negatives from config
        self.records = self.curate_negative_samples(
            all_docs,
            clusters,
            min_negatives=self.split_config.min_negatives,
            max_negatives=self.split_config.max_negatives,
        )
        logger.info(f"Curated {len(self.records)} triplets with negative samples")

        # apply the limit to the records
        if self.split_config.limit:
            length = len(self.records)
            self.records = self.records[: self.split_config.limit]
            logger.info(f"Applied limit {self.split_config.limit} to {length} records")

        # Split records using the notebook's split_data logic with config ratios
        train_records, val_records, eval_records = split_embedding_records(
            self.records, self.split_config
        )

        # Format data for embedding training (triplet format)
        formatted_train_records = format_embedding_training_data(train_records)
        formatted_val_records = format_embedding_training_data(val_records)
        corpus = self.create_eval_corpus(eval_records)
        eval_queries = self.create_eval_queries(eval_records)
        eval_qrels = self.create_eval_qrels(eval_records)

        logger.info(
            f"Split {len(self.records)} records into {len(eval_records)} eval, {len(train_records)} train, {len(val_records)} val"
        )

        db = get_db()
        db.flywheel_runs.update_one(
            {"_id": ObjectId(self.flywheel_run_id)},
            {"$set": {"num_records": len(self.records)}},  # Use validated records count
        )

        # Convert record sets to JSONL format
        train_jsonl_data = "\n".join(json.dumps(record) for record in formatted_train_records)
        val_jsonl_data = "\n".join(json.dumps(record) for record in formatted_val_records)
        corpus_data = "\n".join(json.dumps(record) for record in corpus)
        eval_queries_data = "\n".join(json.dumps(record) for record in eval_queries)

        # For embedding training, create a single dataset with training and validation subdirectories
        # This follows the notebook pattern: training/training.jsonl and validation/validation.jsonl
        embed_dataset_name = f"flywheel-embed-{self.output_dataset_prefix + '-' if self.output_dataset_prefix else ''}{self.workload_id}-{self.ts}"
        embed_uploader = get_backend_provider().datastore(dataset_name=embed_dataset_name)

        # Upload training, validation data to training/ subdirectory (following notebook pattern)
        embed_uploader.upload_data(train_jsonl_data, "training/training.jsonl")
        embed_uploader.upload_data(val_jsonl_data, "validation/validation.jsonl")

        # Create separate evaluation dataset for evaluation tasks
        eval_dataset_name = f"flywheel-embed-eval-{self.output_dataset_prefix + '-' if self.output_dataset_prefix else ''}{self.workload_id}-{self.ts}"
        eval_uploader = get_backend_provider().datastore(dataset_name=eval_dataset_name)
        eval_uploader.upload_data(corpus_data, "corpus.jsonl")
        eval_uploader.upload_data(eval_queries_data, "queries.jsonl")
        eval_uploader.upload_tsv_file(eval_qrels, "qrels/test.tsv")

        logger.info(
            f"Created embedding datasets - Combined training dataset: {embed_dataset_name} "
            f"(Train: {len(train_records)} in training/, Val: {len(val_records)} in validation/), "
            f"Eval dataset: {eval_dataset_name} ({len(eval_records)} records)"
        )

        # Process custom evaluation datasets if provided
        custom_eval_datasets, dataset_name_map = [], {}
        if eval_datasets:
            logger.info(f"Processing {len(eval_datasets)} custom evaluation datasets")
            custom_eval_datasets, dataset_name_map = self.process_custom_eval_dataset(eval_datasets)

        # update the flywheel run with the dataset names
        db.flywheel_runs.update_one(
            {"_id": ObjectId(self.flywheel_run_id)},
            {
                "$set": {
                    "datasets": [
                        {
                            "name": eval_dataset_name,
                            "num_records": len(eval_records),
                            "nmp_uri": eval_uploader.get_file_uri(),
                        },
                        {
                            "name": embed_dataset_name,
                            "num_records": len(train_records),
                            "nmp_uri": embed_uploader.get_file_uri(),
                        },
                        *custom_eval_datasets,
                    ],
                }
            },
        )

        result_datasets = {
            DatasetType.BASE: eval_dataset_name,
            DatasetType.TRAIN: embed_dataset_name,
            DatasetType.CUSTOM: dataset_name_map,
        }

        return result_datasets


class DPODatasetCreator(DatasetCreator):
    """Dataset creator for DPO (Direct Preference Optimization) training.

    DPO training uses preference pairs instead of single responses.
    ICL examples also follow the DPO format: {prompt, chosen_response, rejected_response}
    """

    def split_dpo_records(self, records: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
        """Split DPO records into eval, train, and validation sets using simple random splitting.

        DPO records are preference pairs and don't require stratification by tool type.
        Uses random splitting based on configured ratios.

        Returns:
            Tuple of (eval_records, train_records, val_records)
        """
        if self.split_config.random_seed is not None:
            np.random.seed(self.split_config.random_seed)

        # Shuffle records for random distribution
        shuffled_records = records.copy()
        np.random.shuffle(shuffled_records)

        # Calculate split sizes
        total_records = len(shuffled_records)
        eval_size = min(self.split_config.eval_size, total_records)

        # Split off evaluation set
        eval_records = shuffled_records[:eval_size]
        remaining_records = shuffled_records[eval_size:]

        # Split remaining into train and validation
        val_size = int(len(remaining_records) * self.split_config.val_ratio)
        val_records = remaining_records[:val_size]
        train_records = remaining_records[val_size:]

        return eval_records, train_records, val_records

    def format_dpo_training_data(self, records: list[dict]) -> list[dict]:
        """Format DPO records for training.

        NMP 26.03's BinaryPreferenceDataset schema expects ``prompt`` (string or
        list of ChatMessage) plus ``chosen`` and ``rejected`` (strings) — the
        customizer's dataset validator rejects ``chosen_response`` /
        ``rejected_response`` with ``DatasetFormatError: not valid under any of
        the given schemas``. DFW stores records with the legacy
        ``chosen_response`` / ``rejected_response`` field names (still used by
        the eval path's DPO judge prompt template), so rename them here.

        Note: Records are already validated by _validate_dpo_records().
        """
        return [
            {
                "prompt": record["prompt"],
                "chosen": record["chosen_response"],
                "rejected": record["rejected_response"],
            }
            for record in records
        ]

    def format_dpo_eval_data(self, records: list[dict]) -> list[dict]:
        """Format DPO records for evaluation.

        Renames 'prompt' to 'messages' for evaluation API compatibility:
        {
            "messages": [{"role": "user", "content": "..."}],
            "chosen_response": "...",
            "rejected_response": "..."
        }

        Note: Records are already validated by _validate_dpo_records().
        """
        return [
            {
                "messages": record["prompt"],
                "chosen_response": record["chosen_response"],
                "rejected_response": record["rejected_response"],
            }
            for record in records
        ]

    def _deduplicate_dpo_records(self, records: list[dict]) -> list[dict]:
        """Remove duplicate DPO records based on full content.

        Two DPO records are considered duplicates if they have identical content across
        all three fields: prompt, chosen_response, and rejected_response.

        Args:
            records: List of validated DPO records

        Returns:
            List of unique DPO records (first occurrence kept)
        """
        seen_records = set()
        unique_records = []
        duplicate_count = 0

        for record in records:
            try:
                prompt = record.get("prompt", [])
                chosen = record.get("chosen_response", "")
                rejected = record.get("rejected_response", "")

                # Normalize prompt to a hashable format
                # Handle both string and list of message objects
                if isinstance(prompt, str):
                    # Prompt is a simple string
                    prompt_key = prompt
                elif isinstance(prompt, list):
                    # Prompt is a list of message objects - extract content from all messages
                    prompt_contents = []
                    for msg in prompt:
                        if isinstance(msg, dict) and msg.get("content"):
                            prompt_contents.append(msg["content"])
                    prompt_key = tuple(prompt_contents) if prompt_contents else None
                else:
                    # Unknown format, keep record but skip deduplication
                    unique_records.append(record)
                    continue

                # Create a composite key from all three components
                # This ensures only exact duplicates (same prompt + same responses) are removed
                record_key = (prompt_key, chosen, rejected)

                if prompt_key is not None and record_key not in seen_records:
                    seen_records.add(record_key)
                    unique_records.append(record)
                elif prompt_key is not None and record_key in seen_records:
                    duplicate_count += 1
                else:
                    # Keep records without identifiable content
                    unique_records.append(record)
            except (KeyError, TypeError) as e:
                logger.debug(f"Error in DPO deduplication: {e}")
                unique_records.append(record)

        if duplicate_count > 0:
            logger.info(
                f"Removed {duplicate_count} duplicate DPO records based on full content (prompt + responses)"
            )

        return unique_records

    def _validate_dpo_records(self, records: list[dict]) -> list[dict]:
        """Validate DPO records have required fields and sufficient count.

        Args:
            records: Raw DPO records

        Returns:
            List of valid records with only required fields

        Raises:
            ValueError: If insufficient valid records found
        """
        if not records:
            raise ValueError(f"No DPO records found for workload {self.workload_id}")

        total_records = len(records)
        min_total_records = self.split_config.min_total_records
        eval_size = self.split_config.eval_size
        limit = self.split_config.limit

        logger.info(
            f"Starting DPO validation: {total_records} records, "
            f"min_required={min_total_records}, eval_size={eval_size}, limit={limit}"
        )

        # Validate record count requirements
        if total_records < min_total_records:
            raise ValueError(
                f"Not enough DPO records found for workload {self.workload_id}. "
                f"A minimum of {min_total_records} records is required, but only {total_records} were found."
            )

        if eval_size > total_records:
            raise ValueError(
                f"eval_size ({eval_size}) cannot be larger than the total number of DPO records ({total_records})"
            )

        if limit and limit < min_total_records:
            raise ValueError(
                f"limit ({limit}) cannot be less than the minimum number of records ({min_total_records})"
            )

        # Validate field structure
        required_fields = ["prompt", "chosen_response", "rejected_response"]
        validated = []
        invalid_count = 0

        for i, record in enumerate(records):
            missing_fields = [field for field in required_fields if field not in record]
            if missing_fields:
                logger.warning(f"Skipping DPO record {i}: missing fields {missing_fields}")
                invalid_count += 1
                continue

            # Extract only required fields
            validated.append(
                {
                    "prompt": record["prompt"],
                    "chosen_response": record["chosen_response"],
                    "rejected_response": record["rejected_response"],
                }
            )

        logger.info(f"After field validation: {len(validated)} valid records")

        # Deduplicate records based on prompt content
        deduplicated = self._deduplicate_dpo_records(validated)
        logger.info(f"After deduplication: {len(deduplicated)} unique records")

        # Check if we have enough valid records after deduplication
        if len(deduplicated) < min_total_records:
            raise ValueError(
                f"Insufficient valid DPO records for workload {self.workload_id}. "
                f"Found {len(deduplicated)} unique valid records but need {min_total_records}. "
                f"Total records: {total_records}, invalid format: {invalid_count}, "
                f"after deduplication: {len(deduplicated)}. "
                f"Please provide more valid DPO records with all required fields (prompt, chosen_response, rejected_response)."
            )

        # Apply limit with random sampling (like DataValidator does)
        if self.split_config.random_seed is not None:
            np.random.seed(self.split_config.random_seed)

        # If limit is set, randomly sample records
        if limit and limit < len(deduplicated):
            indices = np.random.choice(len(deduplicated), size=limit, replace=False)
            selected_records = [deduplicated[i] for i in indices]
            logger.info(
                f"Applied limit: selected {len(selected_records)} from {len(deduplicated)} valid records"
            )
        else:
            selected_records = deduplicated

        logger.info(
            f"DPO validation complete: {len(selected_records)} final records from {total_records} total "
            f"({invalid_count} invalid, {len(validated)} valid before dedup, {len(deduplicated)} after dedup)"
        )

        return selected_records

    def create_dpo_datasets(self) -> tuple[str | None, dict[str, str]]:
        """Create datasets for DPO training.

        Returns:
            Tuple of (None, dataset_dict) where dataset_dict contains:
            - BASE: evaluation dataset
            - ICL: evaluation dataset with ICL examples
            - TRAIN: training dataset
        """
        # Validate records (includes count checks and field validation)
        validated_records = self._validate_dpo_records(self.records)

        # Split records using DPO-specific splitting (simple random, no stratification)
        eval_records, train_records, val_records = self.split_dpo_records(validated_records)
        logger.info(
            f"Split {len(validated_records)} DPO records into {len(eval_records)} eval, "
            f"{len(train_records)} train, {len(val_records)} val"
        )

        # Update flywheel run with validated record count
        db = get_db()
        db.flywheel_runs.update_one(
            {"_id": ObjectId(self.flywheel_run_id)},
            {"$set": {"num_records": len(validated_records)}},
        )

        # Format training data (DPO format)
        train_formatted = self.format_dpo_training_data(train_records)
        val_formatted = self.format_dpo_training_data(val_records)

        # Format eval data
        eval_formatted = self.format_dpo_eval_data(eval_records)

        icl_selector = ICLSelection(self.icl_config, self.workload_id, self.client_id)
        index_name, icl_eval_records = icl_selector.generate_icl_records(
            train_records, WorkloadClassification.DPO, eval_formatted
        )

        # Convert to JSONL
        train_jsonl = "\n".join(json.dumps(r) for r in train_formatted)
        val_jsonl = "\n".join(json.dumps(r) for r in val_formatted)
        eval_jsonl = "\n".join(json.dumps(r) for r in eval_formatted)
        icl_jsonl = "\n".join(json.dumps(r) for r in icl_eval_records)

        # Create dataset names
        eval_dataset_name = f"flywheel-dpo-eval-{self.output_dataset_prefix + '-' if self.output_dataset_prefix else ''}{self.workload_id}-{self.ts}"
        icl_dataset_name = f"flywheel-dpo-icl-{self.output_dataset_prefix + '-' if self.output_dataset_prefix else ''}{self.workload_id}-{self.ts}"
        train_dataset_name = f"flywheel-dpo-train-{self.output_dataset_prefix + '-' if self.output_dataset_prefix else ''}{self.workload_id}-{self.ts}"

        # Upload datasets
        eval_uploader = get_backend_provider().datastore(dataset_name=eval_dataset_name)
        eval_uploader.upload_data(eval_jsonl, "eval_data.jsonl")

        icl_uploader = get_backend_provider().datastore(dataset_name=icl_dataset_name)
        icl_uploader.upload_data(icl_jsonl, "eval_data.jsonl")

        train_uploader = get_backend_provider().datastore(dataset_name=train_dataset_name)
        train_uploader.upload_data(train_jsonl, "training/train_data.jsonl")
        train_uploader.upload_data(val_jsonl, "validation/val_data.jsonl")

        # Update flywheel run with dataset info
        db.flywheel_runs.update_one(
            {"_id": ObjectId(self.flywheel_run_id)},
            {
                "$set": {
                    "datasets": [
                        {
                            "name": eval_dataset_name,
                            "num_records": len(eval_formatted),
                            "nmp_uri": eval_uploader.get_file_uri(),
                        },
                        {
                            "name": icl_dataset_name,
                            "num_records": len(icl_eval_records),
                            "nmp_uri": icl_uploader.get_file_uri(),
                        },
                        {
                            "name": train_dataset_name,
                            "num_records": len(train_formatted),
                            "nmp_uri": train_uploader.get_file_uri(),
                        },
                    ],
                }
            },
        )

        logger.info(
            f"Created DPO datasets - Eval: {len(eval_formatted)}, "
            f"ICL: {len(icl_eval_records)}, Train: {len(train_formatted)}, Val: {len(val_formatted)}"
        )

        return (
            index_name,
            {
                DatasetType.BASE: eval_dataset_name,
                DatasetType.ICL: icl_dataset_name,
                DatasetType.TRAIN: train_dataset_name,
            },
        )
