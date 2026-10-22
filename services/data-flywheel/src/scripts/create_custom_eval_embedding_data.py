import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

# Add both the project root and src directory to the path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)
sys.path.insert(0, src_dir)

from src.scripts.utils import resolve_file_path  # noqa: E402


def format_row_to_string(row: dict) -> str:
    """Converts a DataFrame row into the desired '|' separated string format."""

    parts = []
    for column_name, value in row.items():
        safe_value = str(value) if value is not None else ""
        part = f"{column_name} : {safe_value}"
        parts.append(part)

    # Join all parts using the '|' delimiter
    return " | ".join(parts)


def load_corpus(file_path: str | Path) -> pd.DataFrame:
    """
    Load corpus from file path.
    """
    # read all corpus
    # Resolve and read file
    safe_path = resolve_file_path(file_path)
    print(f"Loading corpus from {safe_path}...")
    data = pd.read_csv(safe_path)
    file_extension = safe_path.lower().split(".")[-1]
    print(f"Processing {len(data)} records from {file_extension.upper()} file...")
    return data


def load_eval_records(file_path: str | Path) -> list[dict]:
    """Load evaluation records from a file."""
    safe_path = resolve_file_path(file_path)
    print(f"Loading evaluation records from {safe_path}...")
    if str(safe_path).endswith(".csv"):
        df = pd.read_csv(safe_path)
        records = df.to_dict(orient="records")
    elif str(safe_path).endswith(".jsonl"):
        records = []
        with open(safe_path) as f:
            for line in f:
                records.append(json.loads(line))
    else:
        raise ValueError(f"Unsupported file format: {safe_path}")
    print(f"Loaded {len(records)} evaluation records.")
    return records


def filter_records_in_place(eval_records: list[dict], ids: list[str]) -> list[dict]:
    """
    Filter eval records in place by ids.
    """
    print(f"Filtering records against {len(ids)} corpus IDs...")
    filtered_records = []
    for record in eval_records:
        bugid = str(int(float(record.get("bugid"))))
        if bugid in ids:
            duplicates = str(record.get("Duplicates", ""))
            if not duplicates:
                continue

            pos_doc_ids = [
                str(int(float(part.strip()))) for part in duplicates.split(",") if part.strip()
            ]
            pos_doc_ids = [id for id in pos_doc_ids if id in ids]

            if pos_doc_ids:
                filtered_records.append(
                    {
                        "bugid": str(bugid),
                        "Duplicates": pos_doc_ids,  # Store as list of strings
                    }
                )
    print(f"Filtered down to {len(filtered_records)} records valid in corpus.")
    return filtered_records


def create_eval_corpus(
    eval_records: list[dict], corpus: pd.DataFrame, limit: int | None = None
) -> list[dict]:
    """
    Create eval corpus from eval records.
    """
    print("Creating evaluation corpus...")
    eval_corpus = []
    all_ids = set()

    eval_ids = []
    for _, record in enumerate(eval_records):
        eval_ids.append(record["bugid"])
        # record["Duplicates"] is already a list of strings from filter_records_in_place
        pos_doc_ids = record["Duplicates"]
        eval_ids.extend(pos_doc_ids)

    eval_ids = list(set(eval_ids))
    print(f"Found {len(eval_ids)} unique document IDs relevant to evaluation.")

    # Filter corpus dataframe efficiently
    # Ensure bugid column is string for comparison
    corpus["bugid"] = corpus["bugid"].astype(str)
    filtered_data = corpus[corpus["bugid"].isin(eval_ids)]

    if limit:
        print(f"Applying limit: {limit}")
        filtered_data = filtered_data.head(limit)

    for _, record in filtered_data.iterrows():
        bugid = str(record["bugid"])
        if bugid not in all_ids:
            doc_entry = {
                "_id": bugid,
                "title": "",
                "text": format_row_to_string(record.to_dict()),
            }
            eval_corpus.append(doc_entry)
            all_ids.add(bugid)

    print(f"Created evaluation corpus with {len(eval_corpus)} documents.")
    return eval_corpus


def create_eval_queries(eval_records: list[dict], corpus: pd.DataFrame) -> list[dict]:
    """
    Create queries from eval records.
    """
    print("Creating evaluation queries...")
    queries = []
    # Ensure bugid is string
    corpus["bugid"] = corpus["bugid"].astype(str)

    for _, record in enumerate(eval_records):
        bugid = str(record["bugid"])
        # Get the row as a DataFrame, then convert to dict
        row_df = corpus[corpus["bugid"] == bugid]
        if not row_df.empty:
            # Take the first match and convert to dict
            row_dict = row_df.iloc[0].to_dict()
            text = format_row_to_string(row_dict)
            queries.append({"_id": bugid, "text": text})

    print(f"Created {len(queries)} evaluation queries.")
    return queries


def create_eval_qrels(eval_records: list[dict]) -> list[dict]:
    """
    Create qrels from eval records.
    """
    print("Creating evaluation qrels...")
    qrels = []

    for _, record in enumerate(eval_records):
        query_id = str(record["bugid"])
        # Duplicates is a list of strings from filter_records_in_place
        pos_doc_ids = record["Duplicates"]

        for pos_doc_id in pos_doc_ids:
            qrels.append({"query-id": query_id, "corpus-id": pos_doc_id, "score": 1})

    print(f"Created {len(qrels)} relevance judgments (qrels).")
    return qrels


def store_beir_format(
    output_path: str, eval_corpus: list[dict], eval_queries: list[dict], eval_qrels: list[dict]
) -> None:
    """
    Store BEIR format data in the specified directory.
    """
    print(f"Storing BEIR format data to {output_path}...")
    os.makedirs(output_path, exist_ok=True)

    # Make qrels folder if it doesn't exist (standard BEIR structure usually puts qrels in a subfolder,
    # but this script put it in root. Standard BEIR is corpus.jsonl, queries.jsonl, qrels/test.tsv)
    # Based on load_embedding_eval_data.py (line 419: qrels_file = data_path / "qrels" / "test.tsv")
    # We should probably put it in qrels/test.tsv
    qrels_dir = os.path.join(output_path, "qrels")
    os.makedirs(qrels_dir, exist_ok=True)

    with open(os.path.join(output_path, "corpus.jsonl"), "w") as f:
        for doc in eval_corpus:
            f.write(json.dumps(doc) + "\n")

    with open(os.path.join(output_path, "queries.jsonl"), "w") as f:
        for query in eval_queries:
            f.write(json.dumps(query) + "\n")

    with open(os.path.join(qrels_dir, "test.tsv"), "w") as f:
        f.write("query-id\tcorpus-id\tscore\n")  # Header is usually good practice for TSV
        for qrel in eval_qrels:
            f.write(f"{qrel['query-id']}\t{qrel['corpus-id']}\t{qrel['score']}\n")

    print("Successfully stored all files.")


def create_custom_eval_embedding_data(
    eval_records_path: str | Path,
    corpus_file_path: str | Path,
    output_path: str,
    limit: int | None = None,
) -> None:
    """
    Create custom evaluation embedding data from eval records.
    """
    print("\nStep 1: Loading data...")
    corpus = load_corpus(corpus_file_path)
    eval_records = load_eval_records(eval_records_path)

    # Ensure IDs are strings for consistent comparison
    corpus["bugid"] = corpus["bugid"].astype(str)
    all_ids = set(corpus["bugid"].to_list())
    print("--------\n")

    # Filter eval records in place
    print("Step 2: Filtering records...")
    eval_records = filter_records_in_place(eval_records, all_ids)
    print("--------\n")

    if not eval_records:
        print("Warning: No valid evaluation records found after filtering!")
        return

    print("Step 3: Creating evaluation components...")
    eval_corpus = create_eval_corpus(eval_records, corpus, limit)
    eval_queries = create_eval_queries(eval_records, corpus)
    eval_qrels = create_eval_qrels(eval_records)
    print("--------\n")

    print("Step 4: Storing BEIR format data...")
    store_beir_format(output_path, eval_corpus, eval_queries, eval_qrels)
    print("--------\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create custom evaluation embedding data.")
    parser.add_argument("--eval-records", type=str, required=True, help="Path to eval records file")
    parser.add_argument("--corpus-file-path", type=str, required=True, help="Path to corpus file")
    parser.add_argument("--output-path", type=str, required=True, help="Path to output directory")
    parser.add_argument(
        "--limit", type=int, required=False, help="Limit the number of records to process"
    )
    args = parser.parse_args()
    try:
        create_custom_eval_embedding_data(
            args.eval_records, args.corpus_file_path, args.output_path, args.limit
        )
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
