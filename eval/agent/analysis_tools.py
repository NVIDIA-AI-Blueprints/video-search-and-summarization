import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


def process_all_runs(logs_directory: str) -> List[Dict[str, Any]]:
    """Process all run directories and extract metrics."""
    logs_path = Path(logs_directory)
    all_runs = []

    if not logs_path.exists():
        print(f"Logs directory {logs_directory} does not exist!")
        return []

    # Find all run directories (directories that contain health summary files)
    for item in logs_path.iterdir():
        if item.is_dir():
            health_files = list(item.glob("via_health_summary_*.json"))
            if health_files:  # Only process directories with health summary files
                print(f"Processing run: {item.name}")
                metrics = extract_run_metrics(item)
                all_runs.append(metrics)

    return all_runs


def generate_all_run_reports(logs_dir: str = "logs") -> str:
    """
    Generate comprehensive performance reports for all available runs.

    Args:
        logs_dir: Path to the logs directory (default: "logs")

    Returns:
        Comprehensive performance reports for all runs
    """
    try:
        logs_path = Path(logs_dir)
        if not logs_path.exists():
            return f"Error: Directory {logs_dir} does not exist"

        # Get all run folders
        run_folders = []
        for item in logs_path.iterdir():
            if item.is_dir():
                creation_time = datetime.fromtimestamp(item.stat().st_ctime)
                run_folders.append(
                    {"folder": item.name, "path": str(item), "created": creation_time}
                )
        run_folders.sort(key=lambda x: x["created"], reverse=True)

        if not run_folders:
            return f"No run folders found in {logs_dir}"

        all_reports = []
        all_reports.append("=== MULTI-RUN PERFORMANCE ANALYSIS ===")
        all_reports.append(f"Generated reports for {len(run_folders)} runs (newest first)")

        for i, run_info in enumerate(run_folders):
            all_reports.append(
                f"\n RUN: {run_info['folder']} (created: {run_info['created'].strftime('%Y-%m-%d %H:%M:%S')})"
            )
            all_reports.append("=" * 80)

            report = _generate_run_report(run_info["path"])
            all_reports.append(report)
            all_reports.append("\n" + "=" * 80)

        full_report = "\n".join(all_reports)

        return full_report

    except Exception as e:
        return f"Error generating all reports: {str(e)}"


def extract_gpu_stats(gpu_csv_path: str) -> Dict[str, float]:
    """Extract GPU utilization and memory statistics from CSV file."""
    try:
        df = pd.read_csv(gpu_csv_path)

        # Extract GPU utilization stats (assuming GPU0_Usage column)
        gpu_usage = df["GPU0_Usage"].tolist()
        gpu_mem = df["GPU0_MemUsage"].tolist()

        # Remove zero values for more meaningful stats (when GPU is idle)
        active_gpu_usage = [x for x in gpu_usage if x > 0]

        return {
            "min_gpu_util": min(gpu_usage) if gpu_usage else 0,
            "max_gpu_util": max(gpu_usage) if gpu_usage else 0,
            "avg_gpu_util": statistics.mean(gpu_usage) if gpu_usage else 0,
            "avg_active_gpu_util": statistics.mean(active_gpu_usage) if active_gpu_usage else 0,
            "avg_gpu_mem": statistics.mean(gpu_mem) if gpu_mem else 0,
            "max_gpu_mem": max(gpu_mem) if gpu_mem else 0,
        }
    except Exception as e:
        print(f"Error reading GPU CSV {gpu_csv_path}: {e}")
        return {
            "min_gpu_util": 0,
            "max_gpu_util": 0,
            "avg_gpu_util": 0,
            "avg_active_gpu_util": 0,
            "avg_gpu_mem": 0,
            "max_gpu_mem": 0,
        }


def extract_accuracy_metrics(accuracy_log_path: str) -> Dict[str, Any]:
    """Extract accuracy metrics from accuracy log file."""
    try:
        with open(accuracy_log_path, "r") as f:
            data = json.load(f)
        return {
            "score_summary": data.get("score_summary"),
            "score_vlm": data.get("score_vlm"),
            "avg_chat_score": data.get("avg_chat_score"),
            "video_id": data.get("video_id"),
        }
    except Exception as e:
        print(f"Error reading accuracy log {accuracy_log_path}: {e}")
        return {"score_summary": None, "score_vlm": None, "avg_chat_score": None, "video_id": None}


def extract_csv_metrics(csv_path: str) -> Dict[str, Any]:
    """Extract metrics from CSV result files."""
    try:
        df = pd.read_csv(csv_path)

        # Count responses containing "no information"
        no_info_count = 0
        scores = []

        if "Response" in df.columns and "Score" in df.columns:
            for response in df["Response"]:
                if isinstance(response, str) and "no information" in response.lower():
                    no_info_count += 1

            # Extract all scores
            scores = [float(score) for score in df["Score"] if pd.notna(score)]

        return {
            "no_information_responses": no_info_count,
            "llm_judge_scores": scores,
            "total_qa_pairs": len(df),
            "avg_judge_score": statistics.mean(scores) if scores else 0,
            "min_judge_score": min(scores) if scores else 0,
            "max_judge_score": max(scores) if scores else 0,
        }

    except Exception as e:
        print(f"Error reading CSV {csv_path}: {e}")
        return {
            "no_information_responses": 0,
            "llm_judge_scores": [],
            "total_qa_pairs": 0,
            "avg_judge_score": 0,
            "min_judge_score": 0,
            "max_judge_score": 0,
        }


def extract_run_metrics(run_dir: Path) -> Dict[str, Any]:
    """Extract all metrics from a single run directory."""
    run_id = run_dir.name

    # Parse run directory name to extract components
    parts = run_id.split("_")
    if len(parts) >= 3:
        model_name = parts[0]
        video_name = parts[1]
        chunk_info = parts[2] if len(parts) > 2 else ""
        unique_id = parts[-1] if len(parts) > 3 else ""
    else:
        model_name = "unknown"
        video_name = "unknown"
        chunk_info = ""
        unique_id = run_id

    metrics = {
        "test_case_id": run_id,
        "model_name": model_name,
        "video_name": video_name,
        "unique_id": unique_id,
        "chunk_info": chunk_info,
    }

    # Find health summary JSON file
    health_files = list(run_dir.glob("via_health_summary_*.json"))
    if health_files:
        try:
            with open(health_files[0], "r") as f:
                health_data = json.load(f)

            # Extract performance metrics
            metrics.update(
                {
                    "ca_rag_latency": health_data.get("ca_rag_latency", 0),
                    "decode_latency": health_data.get("decode_latency", 0),
                    "vlm_latency": health_data.get(
                        "vlm_latency", 0
                    ),  # This is likely the summary_latency
                    "vlm_pipeline_latency": health_data.get("vlm_pipeline_latency", 0),
                    "e2e_latency": health_data.get("e2e_latency", 0),
                    "input_vid_duration": health_data.get("input_video_duration", 0),
                    "chunk_size": health_data.get("chunk_size", 0),
                    "num_chunks": health_data.get("num_chunks", 0),
                    "summary_tokens": health_data.get("total_vlm_output_tokens", 0),
                    "input_tokens": health_data.get("total_vlm_input_tokens", 0),
                    "vlm_model_name": health_data.get("vlm_model_name", model_name),
                    "num_gpus": health_data.get("num_gpus", 1),
                    "vlm_batch_size": health_data.get("vlm_batch_size", 1),
                }
            )

            # Extract and convert request start time to human readable format
            req_start_time = health_data.get("req_start_time", 0)
            if req_start_time:
                try:
                    # Convert Unix timestamp to datetime
                    start_datetime = datetime.fromtimestamp(req_start_time)
                    metrics["req_start_time_raw"] = req_start_time
                    metrics["req_start_time_readable"] = start_datetime.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    metrics["req_start_date"] = start_datetime.strftime("%Y-%m-%d")
                    metrics["req_start_time_only"] = start_datetime.strftime("%H:%M:%S")
                except (ValueError, OSError) as e:
                    print(f"Error converting timestamp {req_start_time}: {e}")
                    metrics["req_start_time_raw"] = req_start_time
                    metrics["req_start_time_readable"] = "Invalid timestamp"
                    metrics["req_start_date"] = "Unknown"
                    metrics["req_start_time_only"] = "Unknown"
            else:
                metrics["req_start_time_raw"] = 0
                metrics["req_start_time_readable"] = "Not available"
                metrics["req_start_date"] = "Unknown"
                metrics["req_start_time_only"] = "Unknown"

            # Count summary requests (approximate from number of chunks processed)
            all_times = health_data.get("all_times", [])
            metrics["summary_requests"] = len(all_times)

        except Exception as e:
            print(f"Error reading health summary for {run_id}: {e}")

    # Extract GPU statistics
    gpu_files = list(run_dir.glob("via_gpu_usage_*.csv"))
    if gpu_files:
        gpu_stats = extract_gpu_stats(str(gpu_files[0]))
        metrics.update(gpu_stats)

    # Extract accuracy metrics
    accuracy_files = list(run_dir.glob("accuracy_*.log"))
    if accuracy_files:
        accuracy_stats = extract_accuracy_metrics(str(accuracy_files[0]))
        metrics.update(accuracy_stats)

    # Extract CSV metrics
    csv_files = list(run_dir.glob("*.result.csv"))
    if not csv_files:
        csv_files = list(run_dir.glob("qa_results_*.csv"))
    if csv_files:
        csv_metrics = extract_csv_metrics(str(csv_files[0]))
        metrics.update(csv_metrics)

    return metrics


def format_metrics_for_llm(all_runs: List[Dict[str, Any]]) -> str:
    """Format extracted metrics into a readable text format for LLM analysis."""

    if not all_runs:
        return "No run data found."

    output = []
    output.append("=" * 100)
    output.append("VIDEO SEARCH & SUMMARIZATION PIPELINE - PERFORMANCE ANALYSIS REPORT")
    output.append("=" * 100)
    output.append(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    output.append(f"Total runs analyzed: {len(all_runs)}")
    output.append("")

    # Summary statistics
    models = set(run.get("vlm_model_name", "unknown") for run in all_runs)
    videos = set(run.get("video_name", "unknown") for run in all_runs)

    output.append("OVERVIEW:")
    output.append(f"Models tested: {', '.join(sorted(models))}")
    output.append(f"Videos processed: {', '.join(sorted(videos))}")
    output.append("")

    # Detailed run information
    output.append("DETAILED RUN METRICS:")
    output.append("-" * 100)

    for i, run in enumerate(all_runs, 1):
        output.append(f"\nRUN #{i}: {run.get('test_case_id', 'Unknown')}")
        output.append("-" * 50)

        # Basic info
        output.append(f"Model: {run.get('vlm_model_name', 'N/A')}")
        output.append(f"Video: {run.get('video_name', 'N/A')}")
        output.append(f"Run Started: {run.get('req_start_time_readable', 'N/A')}")
        output.append(f"Video Duration: {run.get('input_vid_duration', 0):.2f}s")
        output.append(f"Chunk Size: {run.get('chunk_size', 0)}s")
        output.append(f"Number of Chunks: {run.get('num_chunks', 0)}")
        output.append(f"Summary Requests: {run.get('summary_requests', 0)}")

        # Performance metrics
        output.append("\nPERFORMANCE METRICS:")
        output.append(f"  End-to-End Latency: {run.get('e2e_latency', 0):.3f}s")
        output.append(f"  CA-RAG Latency: {run.get('ca_rag_latency', 0):.3f}s")
        output.append(f"  Decode Latency: {run.get('decode_latency', 0):.3f}s")
        output.append(f"  VLM/Summary Latency: {run.get('vlm_latency', 0):.3f}s")
        output.append(f"  VLM Pipeline Latency: {run.get('vlm_pipeline_latency', 0):.3f}s")

        # Token information
        output.append("\nTOKEN USAGE:")
        output.append(f"  Summary Tokens (Output): {run.get('summary_tokens', 0)}")
        output.append(f"  Input Tokens: {run.get('input_tokens', 0)}")

        # GPU utilization
        output.append("\nGPU UTILIZATION:")
        output.append(
            f"  Min/Avg/Max GPU Usage: {run.get('min_gpu_util', 0):.1f}% / "
            f"{run.get('avg_gpu_util', 0):.1f}% / {run.get('max_gpu_util', 0):.1f}%"
        )
        output.append(f"  Average Active GPU Usage: {run.get('avg_active_gpu_util', 0):.1f}%")
        output.append(f"  Average GPU Memory: {run.get('avg_gpu_mem', 0):.1f}GB")
        output.append(f"  Peak GPU Memory: {run.get('max_gpu_mem', 0):.1f}GB")

        # Accuracy metrics (if available)
        if run.get("score_summary") is not None:
            output.append("\nACCURACY METRICS:")
            output.append(f"  Summary Score: {run.get('score_summary', 'N/A')}")
            # output.append(f"  VLM Score: {run.get('score_vlm', 'N/A')}")

        # Q&A Evaluation metrics (if available)
        if run.get("total_qa_pairs", 0) > 0:
            output.append(f"  Total Q&A Pairs: {run.get('total_qa_pairs', 0)}")
            output.append(f"  'No Information' Responses: {run.get('no_information_responses', 0)}")
            output.append(
                f"  Chat Accuracy Score Range from LLM Judge: "
                f"{run.get('min_judge_score', 0):.1f} - {run.get('max_judge_score', 0):.1f}"
            )
            output.append(f"  Average Chat Accuracy Score: {run.get('avg_judge_score', 0):.2f}")

            # Include the actual scores list
            scores = run.get("llm_judge_scores", [])
            if scores:
                scores_str = ", ".join([f"{score:.1f}" for score in scores])
                output.append(f"  All LLM Judge Scores: [{scores_str}]")

        # Efficiency metrics
        if run.get("input_vid_duration", 0) > 0 and run.get("e2e_latency", 0) > 0:
            efficiency_ratio = run.get("e2e_latency", 0) / run.get("input_vid_duration", 1)
            output.append("\nEFFICIENCY:")
            output.append(f"  Processing Speed Ratio: {efficiency_ratio:.2f}x (lower is better)")
            if run.get("summary_tokens", 0) > 0 and run.get("vlm_latency", 0) > 0:
                tokens_per_second = run.get("summary_tokens", 0) / run.get("vlm_latency", 1)
                output.append(f"  Tokens per Second: {tokens_per_second:.2f}")

    # Performance comparison table
    if len(all_runs) > 1:
        output.append("\n" + "=" * 130)
        output.append("PERFORMANCE COMPARISON TABLE")
        output.append("=" * 130)

        # Create a simple table
        headers = [
            "Model",
            "Video",
            "Chunk Size",
            "Start Time",
            "E2E (s)",
            "VLM (s)",
            "GPU Avg%",
            "Tokens",
            "No Info",
            "Avg Judge",
        ]
        col_widths = [12, 10, 10, 10, 12, 10, 10, 10, 8, 8, 10]

        # Header
        header_row = " | ".join(h.center(w) for h, w in zip(headers, col_widths))
        output.append(header_row)
        output.append("-" * len(header_row))

        # Data rows
        for run in sorted(all_runs, key=lambda x: x.get("e2e_latency", 0)):
            row_data = [
                str(run.get("vlm_model_name", "N/A"))[:11],
                str(run.get("video_name", "N/A"))[:9],
                str(run.get("chunk_size", "N/A"))[:10],
                str(run.get("req_start_time_only", "N/A"))[:15],
                f"{run.get('e2e_latency', 0):.2f}",
                f"{run.get('vlm_latency', 0):.2f}",
                f"{run.get('avg_gpu_util', 0):.1f}%",
                str(run.get("summary_tokens", 0)),
                str(run.get("no_information_responses", 0)),
                (
                    f"{run.get('avg_judge_score', 0):.1f}"
                    if run.get("avg_judge_score", 0) > 0
                    else "N/A"
                ),
            ]
            row = " | ".join(data.center(w) for data, w in zip(row_data, col_widths))
            output.append(row)

    output.append("\n" + "=" * 100)
    output.append("END OF REPORT")
    output.append("=" * 100)

    return "\n".join(output)


def _generate_run_report(run_path: str) -> str:
    """Generate a comprehensive report for a single run with all calculations done"""
    try:
        run_folder = Path(run_path).name

        # Load health summary
        health_data = None
        for file in Path(run_path).glob("*"):
            if file.name.startswith("via_health_summary_") and file.name.endswith(".json"):
                with open(file, "r") as f:
                    health_data = json.load(f)
                break

        if not health_data:
            return f"No health summary found in {run_folder}"

        # Start building the comprehensive report
        report = []
        report.append(f"=== VSS PERFORMANCE REPORT: {run_folder} ===")

        # Hardware Configuration
        report.append("\n HARDWARE CONFIGURATION:")
        report.append(
            f"   GPUs: {health_data.get('num_gpus', 'N/A')} x "
            f"{health_data.get('gpu_names', ['Unknown'])[0] if health_data.get('gpu_names') else 'Unknown'}"
        )
        report.append(f"   VLM Model: {health_data.get('vlm_model_name', 'N/A')}")
        report.append(f"   VLM Batch Size: {health_data.get('vlm_batch_size', 'N/A')}")

        # Video Processing Configuration
        report.append("\n📹 VIDEO PROCESSING CONFIGURATION:")
        report.append(f"   Video Duration: {health_data.get('input_video_duration', 'N/A')}s")
        report.append(f"   Chunk Size: {health_data.get('chunk_size', 'N/A')}s")
        report.append(f"   Number of Chunks: {int(health_data.get('num_chunks', 0))}")
        report.append(f"   Chunk Overlap: {health_data.get('chunk_overlap_duration', 'N/A')}s")

        # Overall Performance Metrics
        report.append("\n⏱️ OVERALL PERFORMANCE METRICS:")
        e2e_latency = health_data.get("e2e_latency", 0)
        decode_latency = health_data.get("decode_latency", 0)
        vlm_latency = health_data.get("vlm_latency", 0)
        pipeline_latency = health_data.get("vlm_pipeline_latency", 0)
        rag_latency = health_data.get("ca_rag_latency", 0)
        req_start_time = health_data.get("req_start_time", 0)

        report.append(
            f"   Request Start Time: {datetime.fromtimestamp(req_start_time).strftime('%Y-%m-%d %H:%M:%S')}"
        )
        report.append(f"   End-to-End Latency: {e2e_latency:.2f}s")
        report.append(
            f"   Decode Latency: {decode_latency:.2f}s "
            f"({(decode_latency/(e2e_latency or 1)*100):.1f}% of total)"
        )
        report.append(
            f"   VLM Latency: {vlm_latency:.2f}s "
            f"({(vlm_latency/(e2e_latency or 1)*100):.1f}% of total)"
        )
        report.append(f"   VLM Pipeline Latency: {pipeline_latency:.2f}s")
        report.append(f"   CA RAG Latency: {rag_latency:.2f}s")

        # Processing Efficiency
        video_duration = health_data.get("input_video_duration", 0)
        if video_duration > 0:
            efficiency = video_duration / (e2e_latency or 1)
            report.append(f"   Processing Efficiency: {efficiency:.1f}x real-time")

        # Token Statistics
        report.append("\n📝 TOKEN PROCESSING:")
        total_input = health_data.get("total_vlm_input_tokens", 0)
        total_output = health_data.get("total_vlm_output_tokens", 0)
        report.append(f"   Total Input Tokens: {total_input:,}")
        report.append(f"   Total Output Tokens: {total_output:,}")
        if vlm_latency > 0:
            input_rate = total_input / vlm_latency
            output_rate = total_output / vlm_latency
            report.append(f"   Input Token Rate: {input_rate:.1f} tokens/sec")
            report.append(f"   Output Token Rate: {output_rate:.1f} tokens/sec")

        # Detailed Chunk Analysis
        if "all_times" in health_data:
            report.append("\n🔍 DETAILED CHUNK ANALYSIS:")
            chunks = health_data["all_times"]

            chunk_details = []
            for chunk in chunks:
                chunk_id = chunk.get("chunk_id", "Unknown")

                # Calculate decode time
                decode_time = 0
                if chunk.get("decode_start") and chunk.get("decode_end"):
                    decode_time = chunk["decode_end"] - chunk["decode_start"]

                # Calculate VLM time
                vlm_time = 0
                if chunk.get("vlm_start") and chunk.get("vlm_end"):
                    vlm_time = chunk["vlm_end"] - chunk["vlm_start"]

                # Calculate add_doc time
                add_doc_time = 0
                if chunk.get("add_doc_start") and chunk.get("add_doc_end"):
                    add_doc_time = chunk["add_doc_end"] - chunk["add_doc_start"]

                total_chunk_time = decode_time + vlm_time + add_doc_time

                # Token stats
                vlm_stats = chunk.get("vlm_stats", {})
                input_tokens = vlm_stats.get("input_tokens", 0)
                output_tokens = vlm_stats.get("output_tokens", 0)

                chunk_details.append(
                    {
                        "id": chunk_id,
                        "decode_time": decode_time,
                        "vlm_time": vlm_time,
                        "add_doc_time": add_doc_time,
                        "total_time": total_chunk_time,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    }
                )

            # Sort by total time to identify slowest
            chunk_details.sort(key=lambda x: x["total_time"], reverse=True)

            for i, chunk in enumerate(chunk_details):
                status = (
                    "🐌 SLOWEST"
                    if i == 0
                    else "🚀 FASTEST" if i == len(chunk_details) - 1 else f"#{i+1}"
                )
                report.append(f"   {status} Chunk {chunk['id']}:")
                report.append(f"      Total Time: {chunk['total_time']:.2f}s")
                report.append(
                    f"      Decode: {chunk['decode_time']:.2f}s, VLM: {chunk['vlm_time']:.2f}s, "
                    f"Add Doc: {chunk['add_doc_time']:.3f}s"
                )
                report.append(
                    f"      Tokens: {chunk['input_tokens']} in → {chunk['output_tokens']} out"
                )
                if chunk["vlm_time"] > 0:
                    token_rate = chunk["output_tokens"] / chunk["vlm_time"]
                    report.append(f"      Output Rate: {token_rate:.1f} tokens/sec")

            # Bottleneck Analysis
            avg_decode = statistics.mean([c["decode_time"] for c in chunk_details])
            avg_vlm = statistics.mean([c["vlm_time"] for c in chunk_details])

            report.append("\n🎯 BOTTLENECK ANALYSIS:")
            if avg_decode > avg_vlm:
                report.append(
                    f"   PRIMARY BOTTLENECK: Decode ({avg_decode:.2f}s avg vs "
                    f"{avg_vlm:.2f}s VLM avg)"
                )
                report.append(
                    f"   Decode is {(avg_decode/(avg_vlm or 1)):.1f}x slower than "
                    f"VLM on average"
                )
            else:
                report.append(
                    f"   PRIMARY BOTTLENECK: VLM ({avg_vlm:.2f}s avg vs "
                    f"{avg_decode:.2f}s decode avg)"
                )
                report.append(
                    f"   VLM is {(avg_vlm/(avg_decode or 1)):.1f}x slower than "
                    f"decode on average"
                )

            # Performance Consistency
            decode_std = (
                statistics.stdev([c["decode_time"] for c in chunk_details])
                if len(chunk_details) > 1
                else 0
            )
            vlm_std = (
                statistics.stdev([c["vlm_time"] for c in chunk_details])
                if len(chunk_details) > 1
                else 0
            )

            report.append("\n📊 PERFORMANCE CONSISTENCY:")
            report.append(f"   Decode Time Variation: ±{decode_std:.2f}s")
            report.append(f"   VLM Time Variation: ±{vlm_std:.2f}s")

            if decode_std > 2.0:
                report.append("   ⚠️ High decode time variation detected")
            if vlm_std > 1.0:
                report.append("   ⚠️ High VLM time variation detected")

        # Load and format GPU usage data
        report.append("\n🔥 GPU UTILIZATION ANALYSIS:")

        gpu_csv = None
        for file in Path(run_path).glob("*"):
            if file.name.startswith("via_gpu_usage_") and file.name.endswith(".csv"):
                gpu_csv = file
                break

        if gpu_csv:
            gpu_df = pd.read_csv(gpu_csv)

            # GPU Usage Summary
            gpu_usage_cols = [col for col in gpu_df.columns if col.endswith("_Usage")]
            for i, col in enumerate(gpu_usage_cols):
                gpu_data = gpu_df[col].dropna()
                report.append(
                    f"   GPU{i} Usage: avg={gpu_data.mean():.1f}%, "
                    f"max={gpu_data.max():.1f}%, min={gpu_data.min():.1f}%"
                )

            # GPU Memory Summary
            gpu_memory_cols = [col for col in gpu_df.columns if col.endswith("_MemUsage")]
            for i, col in enumerate(gpu_memory_cols):
                gpu_mem_data = gpu_df[col].dropna()
                report.append(
                    f"   GPU{i} Memory: avg={gpu_mem_data.mean()/1024:.1f}GB, "
                    f"peak={gpu_mem_data.max()/1024:.1f}GB"
                )

            # GPU Usage Timeline (simplified for LLM readability)
            if len(gpu_df) > 10:
                # Sample 10 evenly spaced points for timeline
                sample_indices = [int(i * len(gpu_df) / 10) for i in range(10)]
                report.append("\n   GPU Usage Timeline (10 sample points):")
                for idx in sample_indices:
                    time = (
                        gpu_df.iloc[idx]["elapsed_time"]
                        if "elapsed_time" in gpu_df.columns
                        else idx
                    )
                    usage_values = [
                        f"GPU{i}:{gpu_df.iloc[idx][col]:.0f}%"
                        for i, col in enumerate(gpu_usage_cols)
                    ]
                    report.append(f"   t={time:.1f}s: {', '.join(usage_values)}")

        # Load and format NVDEC usage data
        nvdec_csv = None
        for file in Path(run_path).glob("*"):
            if file.name.startswith("via_nvdec_usage_") and file.name.endswith(".csv"):
                nvdec_csv = file
                break

        if nvdec_csv:
            report.append("\n🎥 NVDEC UTILIZATION ANALYSIS:")
            nvdec_df = pd.read_csv(nvdec_csv)

            nvdec_cols = [col for col in nvdec_df.columns if "NVDEC" in col]
            for i, col in enumerate(nvdec_cols):
                nvdec_data = nvdec_df[col].dropna()
                active_time_pct = (nvdec_data > 0).sum() / (len(nvdec_data) or 1) * 100
                report.append(
                    f"   GPU{i} NVDEC: avg={nvdec_data.mean():.1f}%, "
                    f"max={nvdec_data.max():.1f}%, active_time={active_time_pct:.1f}%"
                )

        return "\n".join(report)

    except Exception as e:
        return f"Error generating report for {run_path}: {str(e)}"


def generate_single_run_report(run_folder: str = None, logs_dir: str = "logs") -> str:
    """
    Generate a comprehensive performance report for a single run.

    Args:
        run_folder: Name of the run folder to analyze (if None, uses latest run)
        logs_dir: Path to the logs directory (default: "logs")

    Returns:
        Comprehensive performance report with all calculations done
    """
    try:
        logs_path = Path(logs_dir)
        if not logs_path.exists():
            return f"Error: Directory {logs_dir} does not exist"

        # Get run folders
        run_folders = []
        for item in logs_path.iterdir():
            if item.is_dir():
                creation_time = datetime.fromtimestamp(item.stat().st_ctime)
                run_folders.append(
                    {"folder": item.name, "path": str(item), "created": creation_time}
                )
        run_folders.sort(key=lambda x: x["created"], reverse=True)

        # Determine target run
        if run_folder is None or run_folder == "null" or run_folder == "None":
            if not run_folders:
                return f"No run folders found in {logs_dir}"
            run_path = run_folders[0]["path"]
        else:
            run_path = str(logs_path / run_folder)
            if not Path(run_path).exists():
                return f"Run folder '{run_folder}' not found in {logs_dir}"

        report = _generate_run_report(run_path)

        return report

    except Exception as e:
        return f"Error generating report: {str(e)}"
