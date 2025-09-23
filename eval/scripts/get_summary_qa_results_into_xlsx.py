######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

import glob
import json
import os
import sys
from collections import defaultdict

import pandas as pd
import yaml

# VSS Accuracy Scores
# Score	Criteria	Color
# Score 1:	The answer is completely unrelated to the reference.	RED >=1 && <=3
# Score 3:	The answer has minor relevance but does not align with the reference. ORANGE	>3 && <5
# Score 5:	The answer has moderate relevance but contains inaccuracies. ORANGE	>=5 && <7
# Score 7:	The answer aligns with the reference but has minor errors or omissions. GREEN	>=7 && <10
# Score 10:	The answer is completely accurate and aligns perfectly with the reference. GREEN	10


def extract_model_video_info(folder_path):
    """Extract model name and video name from folder name and input_test_config.json."""
    # First try to get video name from input_test_config.json
    config_file = os.path.join(folder_path, "input_test_config.json")
    video_name = None

    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config_data = json.load(f)
                if config_data and isinstance(config_data, dict):
                    video_name = config_data.get("video_id")
        except Exception as e:
            print(f"Error reading config file {config_file}: {e}")

    # Parse folder name for model and UUID: {model}_{video}_{chunks}_{uuid}
    basename = os.path.basename(folder_path)
    parts = basename.split("_")

    if len(parts) < 4:
        return None, None, None

    # Last part is UUID
    uuid = parts[-1]
    # First part is model name
    model_name = parts[0]

    # If we couldn't get video name from config, fall back to parsing folder name
    if video_name is None:
        # Everything in between is video name (fallback)
        video_name = "_".join(parts[1:-2])

    return model_name, video_name, uuid


def find_result_files(base_path):
    """Find all result.csv files recursively."""
    return glob.glob(os.path.join(base_path, "**/*.result.csv"), recursive=True)


def read_text_file(file_path):
    """Read text from file, handling errors."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return ""


def read_accuracy_log(file_path):
    """Read and parse accuracy log file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = f.read().strip()
            return json.loads(data)
    except Exception as e:
        print(f"Error reading accuracy log {file_path}: {e}")

        return {"score_summary": 0, "avg_chat_score": 0, "unique_test_code": ""}


def process_results(base_path):
    """Process all result files and organize by video."""
    result_files = find_result_files(base_path)

    # Group by video name
    video_data = defaultdict(
        lambda: {
            "qa_pairs": None,
            "models": defaultdict(dict),
            "summary_data": defaultdict(dict),
            "accuracy_data": defaultdict(dict),
            "dc_eval": None,  # Will store DC ground truth and model responses
            "config_data": None,  # Will store input_test_config.json data (shared)
            "model_configs": defaultdict(dict),  # Will store config data per model
        }
    )

    # First pass: collect all model+video+uuid combinations to detect duplicates
    model_video_uuid = []
    for file_path in result_files:
        folder_path = os.path.dirname(file_path)
        model_name, video_name, uuid = extract_model_video_info(folder_path)
        if model_name and video_name:
            model_video_uuid.append((model_name, video_name, uuid, file_path, folder_path))

    # Create mapping for models that need UUID suffixes
    model_counts = defaultdict(lambda: defaultdict(list))
    for model_name, video_name, uuid, file_path, folder_path in model_video_uuid:
        model_counts[video_name][model_name].append(uuid)

    # Create model name mapping
    model_name_mapping = {}
    for video_name, models in model_counts.items():
        for model_name, uuids in models.items():
            if len(uuids) > 1:
                # Multiple runs for this model within this video, add UUID suffix to ALL instances
                for uuid in uuids:
                    original_key = (model_name, video_name, uuid)
                    modified_name = f"{model_name} - {uuid[:4]}"
                    model_name_mapping[original_key] = modified_name
            else:
                # Single run for this model within this video, keep original name
                uuid = uuids[0]
                original_key = (model_name, video_name, uuid)
                model_name_mapping[original_key] = model_name

    # Second pass: process files with correct model names
    for model_name, video_name, uuid, file_path, folder_path in model_video_uuid:
        folder_name = os.path.basename(folder_path)
        print(f"Reading folder {folder_name}")

        # Get the appropriate model name (with UUID suffix if needed)
        effective_model_name = model_name_mapping.get((model_name, video_name, uuid), model_name)

        try:
            # Process QA results
            df = pd.read_csv(file_path)

            # Store QA pairs if not already stored for this video
            if video_data[video_name]["qa_pairs"] is None:
                try:
                    video_data[video_name]["qa_pairs"] = df[["Question", "Answer"]].copy()
                except Exception:
                    try:
                        video_data[video_name]["qa_pairs"] = df[["question", "answer"]].copy()
                    except Exception as e:
                        return e

            # Store model responses and scores
            try:
                video_data[video_name]["models"][effective_model_name] = df[
                    ["Response", "Score"]
                ].copy()
            except Exception:
                try:
                    video_data[video_name]["models"][effective_model_name] = df[
                        ["response", "score"]
                    ].copy()
                except Exception as e:
                    return e
            """"
            # Look for summary groundtruth
            gt_summary_files = glob.glob(os.path.join(folder_path, "summary_gt*"))
            if gt_summary_files:
                try:
                    video_data[video_name]["summary_gt"] = read_text_file(gt_summary_files[0])
                except Exception as e:
                    print(f"Error reading summary groundtruth {gt_summary_files[0]}: {e}")


            # Look for model summary
            summ_pattern = os.path.join(folder_path, f"summ_testdata_{uuid}.txt")
            summ_files = glob.glob(summ_pattern)
            if summ_files:
                video_data[video_name]["summary_data"][effective_model_name]["response"] = read_text_file(
                    summ_files[0]
                )
            """

            # Look for DC ground truth - only once per video (shared across models)
            if video_data[video_name]["dc_eval"] is None:
                dc_gt_files = glob.glob(os.path.join(folder_path, "dc_gt.txt"))
                if dc_gt_files:
                    try:
                        dc_gt_content = read_text_file(dc_gt_files[0])
                        if dc_gt_content:
                            video_data[video_name]["dc_eval"] = {
                                "ground_truth": dc_gt_content,
                                "models": {},
                                "scores_per_model": {},  # Store scores per model
                            }
                    except Exception as e:
                        print(f"Error reading DC ground truth {dc_gt_files[0]}: {e}")

            # Look for DC scores for THIS specific model run
            if video_data[video_name]["dc_eval"] is not None:
                dc_scores_files = glob.glob(os.path.join(folder_path, "dc_scores.csv"))
                if dc_scores_files:
                    try:
                        dc_scores_content = read_text_file(dc_scores_files[0])
                        if dc_scores_content.strip():  # Only store non-empty scores
                            video_data[video_name]["dc_eval"]["scores_per_model"][
                                effective_model_name
                            ] = dc_scores_content
                    except Exception as e:
                        print(f"Error reading DC scores {dc_scores_files[0]}: {e}")

            # Look for VLM dense caption output for this model
            vlm_dc_files = glob.glob(os.path.join(folder_path, f"vlm_testdata_{uuid}.txt"))
            if vlm_dc_files and video_data[video_name]["dc_eval"] is not None:
                try:
                    vlm_dc_content = read_text_file(vlm_dc_files[0])
                    if vlm_dc_content:
                        video_data[video_name]["dc_eval"]["models"][
                            effective_model_name
                        ] = vlm_dc_content
                except Exception as e:
                    print(f"Error reading VLM DC output {vlm_dc_files[0]}: {e}")

            # Look for accuracy log
            accuracy_files = glob.glob(os.path.join(folder_path, "accuracy_*.log"))
            if accuracy_files:
                accuracy_data = read_accuracy_log(accuracy_files[0])
                video_data[video_name]["summary_data"][effective_model_name]["score"] = (
                    accuracy_data.get("score_summary", 0)
                )
                video_data[video_name]["accuracy_data"][effective_model_name] = accuracy_data

            # Look for input_test_config.json and store config data
            config_file = os.path.join(folder_path, "input_test_config.json")
            if os.path.exists(config_file):
                try:
                    with open(config_file, "r", encoding="utf-8") as f:
                        config_data = json.load(f)
                        # Store shared config data (only once per video)
                        if video_data[video_name]["config_data"] is None:
                            video_data[video_name]["config_data"] = config_data
                        # Store per-model config data
                        video_data[video_name]["model_configs"][effective_model_name] = config_data
                except Exception as e:
                    print(f"Error reading config file {config_file}: {e}")

            # Look for via_engine.log to get timestamp
            via_engine_log_files = glob.glob(os.path.join(folder_path, "via_engine.log"))
            if via_engine_log_files:
                try:
                    log_content = read_text_file(via_engine_log_files[0])
                    if log_content:
                        # Extract timestamp from first line using basic string operations
                        first_line = log_content.split("\n")[0]
                        timestamp_str = ""

                        # Look for date pattern YYYY-MM-DD followed by time HH:MM:SS
                        try:
                            # Find potential date patterns
                            words = first_line.split()
                            for i, word in enumerate(words):
                                # Check if word looks like a date (YYYY-MM-DD)
                                if len(word) == 10 and word.count("-") == 2:
                                    parts = word.split("-")
                                    if (
                                        len(parts) == 3
                                        and len(parts[0]) == 4
                                        and parts[0].isdigit()
                                        and len(parts[1]) == 2
                                        and parts[1].isdigit()
                                        and len(parts[2]) == 2
                                        and parts[2].isdigit()
                                    ):
                                        # Found date, now look for time in next word
                                        if i + 1 < len(words):
                                            time_word = words[i + 1]
                                            if ":" in time_word and len(time_word.split(":")) >= 2:
                                                time_parts = time_word.split(":")
                                                if (
                                                    len(time_parts[0]) == 2
                                                    and time_parts[0].isdigit()
                                                    and len(time_parts[1]) == 2
                                                    and time_parts[1].isdigit()
                                                ):
                                                    timestamp_str = (
                                                        f"{word} {time_parts[0]}:{time_parts[1]}"
                                                    )
                                                    break

                            # If no date found, just look for time pattern HH:MM:SS
                            if not timestamp_str:
                                for word in words:
                                    if ":" in word and len(word.split(":")) >= 2:
                                        time_parts = word.split(":")
                                        if (
                                            len(time_parts) >= 2
                                            and len(time_parts[0]) == 2
                                            and time_parts[0].isdigit()
                                            and len(time_parts[1]) == 2
                                            and time_parts[1].isdigit()
                                        ):
                                            timestamp_str = f"{time_parts[0]}:{time_parts[1]}"
                                            break
                        except Exception:
                            timestamp_str = ""

                        # Store timestamp in accuracy data
                        if effective_model_name in video_data[video_name]["accuracy_data"]:
                            video_data[video_name]["accuracy_data"][effective_model_name][
                                "timestamp"
                            ] = timestamp_str
                        else:
                            video_data[video_name]["accuracy_data"][effective_model_name] = {
                                "timestamp": timestamp_str
                            }
                except Exception as e:
                    print(f"Error reading via_engine.log {via_engine_log_files[0]}: {e}")

        except Exception as e:
            print(f"Error processing {file_path}: {e}")

    return video_data


# new helper function to parse unique_test_code and extract chunk size
def _parse_unique_test_code(unique_code):
    """Parse the unique_test_code string into components.

    Expected pattern: {model}_{video_filename}_{chunk_size}_{uuid}
    The video filename itself may contain underscores, so we take:
    - model = first segment
    - chunk_size = second to last segment (should be an int/str)
    - uuid = last segment
    - video_filename = everything in between
    Returns a tuple (model, video_filename, chunk_size, uuid).
    If parsing fails, returns (None, None, None, None).
    """
    try:
        parts = unique_code.split("_")
        if len(parts) < 4:
            return None, None, None, None
        model = parts[0]
        chunk_size = parts[-2]
        uuid = parts[-1]
        video_filename = "_".join(parts[1:-2])
        return model, video_filename, chunk_size, uuid
    except Exception:
        return None, None, None, None


# new sheet summarising configurations and chunk size
def create_config_sheet(writer, video_data, byov_config_file):
    """Create a sheet that lists all configurations (model runs) and the chunk size used for each video."""
    workbook = writer.book
    worksheet = workbook.add_worksheet("Configurations")

    # Header format
    header_format = workbook.add_format(
        {"bold": True, "align": "center", "bg_color": "#EEECE1", "border": 1}
    )
    title_format = workbook.add_format(
        {"bold": True, "font_size": 16, "align": "center", "bg_color": "#75BA00", "border": 1}
    )

    row = 0
    worksheet.merge_range(row, 0, row, 6, "Configurations", title_format)
    row += 1

    cfg_headers = [
        "Config #",
        "Model",
        "Batch Size",
        "Frames per Chunk",
        "CA RAG Config",
        "Guardrails Enabled",
        "Guardrail Config File",
    ]
    for col, header in enumerate(cfg_headers):
        worksheet.write(row, col, header, header_format)
    row += 1
    configs = defaultdict(dict)
    with open(byov_config_file, "r") as f:
        configs = yaml.safe_load(f)
        vss_configs = configs["VSS_Configurations"]

        for i, cfg_body in enumerate(vss_configs):

            vlm_cfg = cfg_body["VLM_Configurations"]
            worksheet.write(row, 0, i + 1)
            if vlm_cfg["model"] == "custom":
                worksheet.write(row, 1, vlm_cfg["model_path"].split("/")[-1])
            elif vlm_cfg["model"] == "openai-compat":
                worksheet.write(row, 1, vlm_cfg["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"])
            else:
                worksheet.write(row, 1, vlm_cfg["model"])
            if vlm_cfg["model"] == "nvila":
                if vlm_cfg["VLM_batch_size"] is not None:
                    worksheet.write(row, 2, vlm_cfg["VLM_batch_size"])
                else:
                    worksheet.write(row, 2, "default")
            else:
                worksheet.write(row, 2, "n/a")

            if vlm_cfg["frames_per_chunk"] is not None:
                worksheet.write(row, 3, vlm_cfg["frames_per_chunk"])
            else:
                worksheet.write_url(
                    row,
                    3,
                    (
                        "https://docs.nvidia.com/vss/latest/content/"
                        "vss_configuration.html#vlm-default-number-of-frames-per-chunk"
                    ),
                    string="default",
                )

            worksheet.write(row, 4, cfg_body["CA_RAG_CONFIG"])
            worksheet.write(row, 5, cfg_body["Guardrail_Configurations"]["enable"])
            worksheet.write(row, 6, cfg_body["Guardrail_Configurations"]["guardrail_config_file"])

            # Increment row for next configuration
            row += 1
        # Autofit columns at the end
        if hasattr(worksheet, "autofit"):
            worksheet.autofit()


def create_summary_sheet(writer, video_data):
    """Create a summary sheet with overview organized by video."""
    workbook = writer.book
    worksheet = workbook.add_worksheet("Summary")

    # Define formats
    title_format = workbook.add_format(
        {"bold": True, "font_size": 16, "align": "center", "bg_color": "#75BA00", "border": 1}
    )
    video_header_format = workbook.add_format(
        {"bold": True, "font_size": 14, "align": "center", "bg_color": "#EEECE1", "border": 1}
    )
    column_header_format = workbook.add_format(
        {"bold": True, "align": "center", "bg_color": "#EEECE1", "border": 1, "text_wrap": True}
    )
    regular_format = workbook.add_format({"border": 1, "align": "center"})

    # Color formats for score-based highlighting
    red_format = workbook.add_format({"bg_color": "#FFC7CE", "border": 1, "align": "center"})
    orange_format = workbook.add_format({"bg_color": "#FFEB9C", "border": 1, "align": "center"})
    green_format = workbook.add_format({"bg_color": "#C6EFCE", "border": 1, "align": "center"})

    # Add main title
    worksheet.merge_range("A1:D1", "Test Results Summary", title_format)

    worksheet.set_column("E:E", 10)  # Model
    # Add legend
    worksheet.write(1, 5, "Legend:", workbook.add_format({"bold": True, "align": "center"}))
    worksheet.write(2, 5, "Red (1-3): Unrelated", red_format)
    worksheet.write(3, 5, "Orange (4-6): Minor relevance", orange_format)
    worksheet.write(4, 5, "Green (7-10): Good alignment", green_format)
    worksheet.write(
        5,
        5,
        "N/A: No VLM score",
        workbook.add_format({"bg_color": "#FFFFFF", "border": 1, "align": "center"}),
    )
    worksheet.autofit()

    # Set column widths
    worksheet.set_column("A:A", 25)  # Model
    worksheet.set_column("B:B", 15)  # Score Summary
    worksheet.set_column("C:C", 15)  # Avg Chat Score
    worksheet.set_column("D:D", 15)  # VLM Score

    current_row = 3

    # Process each video separately
    for video_name, data in video_data.items():
        if not data["models"]:
            continue

        # Video header
        worksheet.merge_range(current_row, 0, current_row, 3, f"{video_name}", video_header_format)
        current_row += 1

        # Column headers for this video section
        headers = ["Model", "Summary Score", "Chat Score", "VLM Score"]
        for col, header in enumerate(headers):
            worksheet.write(current_row, col, header, column_header_format)
        current_row += 1

        # Collect model data for this video
        video_models = []
        for model_name in data["models"].keys():
            accuracy = data["accuracy_data"].get(model_name, {})
            video_models.append(
                {
                    "model": model_name,
                    "score_summary": accuracy.get("score_summary", 0),
                    "avg_chat_score": accuracy.get("avg_chat_score", 0),
                    "score_vlm": accuracy.get("score_vlm", 0) if "score_vlm" in accuracy else None,
                }
            )

        # Write model data for this video with score-based color coding
        start_row = current_row
        for model_data in video_models:
            # Model name
            worksheet.write(current_row, 0, model_data["model"], regular_format)

            # Score Summary - color based on score range
            worksheet.write(current_row, 1, model_data["score_summary"], regular_format)

            # Avg Chat Score - color based on score range
            worksheet.write(current_row, 2, round(model_data["avg_chat_score"], 1), regular_format)

            # VLM Score - color based on score range (if available)
            if (
                model_data["score_vlm"] is not None
                and model_data["score_vlm"] != "evaluate_dc error"
            ):
                worksheet.write(current_row, 3, round(model_data["score_vlm"], 1), regular_format)
            else:
                worksheet.write(current_row, 3, "N/A", regular_format)

            current_row += 1

        # Apply conditional formatting for score ranges
        end_row = current_row - 1
        if start_row <= end_row:
            # Apply formatting to all score columns (B, C, D)
            for col in [1, 2, 3]:  # Score Summary, Chat Score, VLM Score

                # GREEN formatting (7-10) - apply first so it has lowest priority
                worksheet.conditional_format(
                    start_row,
                    col,
                    end_row,
                    col,
                    {
                        "type": "cell",
                        "criteria": "between",
                        "minimum": 7,
                        "maximum": 10,
                        "format": green_format,
                    },
                )

                # ORANGE formatting (>3 and <7) - more specific range
                worksheet.conditional_format(
                    start_row,
                    col,
                    end_row,
                    col,
                    {
                        "type": "cell",
                        "criteria": "between",
                        "minimum": 3.001,
                        "maximum": 6.999,
                        "format": orange_format,
                    },
                )

                # RED formatting (1-3) - most specific, highest priority
                worksheet.conditional_format(
                    start_row,
                    col,
                    end_row,
                    col,
                    {
                        "type": "cell",
                        "criteria": "between",
                        "minimum": 1,
                        "maximum": 3,
                        "format": red_format,
                    },
                )

        # Add spacing between videos
        current_row += 1


def create_excel_report(video_data, output_path, byov_config_file):
    """Create Excel report with one sheet per video."""

    with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
        # Create summary sheet first
        create_summary_sheet(writer, video_data)
        # Create configurations sheet
        create_config_sheet(writer, video_data, byov_config_file)
        for video_name, data in video_data.items():
            if data["qa_pairs"] is None or not data["models"]:
                continue

            # Start with question and expected answer columns
            result_df = data["qa_pairs"].copy()
            result_df.rename(columns={"answer": "expected answer"}, inplace=True)

            # Add model data as columns for QA pairs
            for model_name, model_data in data["models"].items():
                # Create columns for each model
                try:
                    for col in ["Response", "Score"]:
                        col_name = f"{model_name}_{col}"
                        result_df[col_name] = model_data[col].values
                except Exception:
                    print("failed for capital letters")
                    try:
                        for col in ["response", "score"]:
                            col_name = f"{model_name}_{col}"
                            result_df[col_name] = model_data[col].values
                            print("worked for lowercase letters")
                    except Exception as e:
                        print("failed for lowercase letters")
                        return e

            """
            # Add model data as columns for summary row
            for model_name in data["models"].keys():
                summary_response = data["summary_data"].get(model_name, {}).get("response", "")
                summary_score = data["summary_data"].get(model_name, {}).get("score", 0)

                summary_row[f"{model_name}_response"] = [summary_response]
                summary_row[f"{model_name}_score"] = [summary_score]

            # Combine summary row with QA data
            result_df = pd.concat([summary_row, result_df], ignore_index=True)
            """

            # Get sheet name
            sheet_name = video_name[:31]  # Excel limits sheet names to 31 chars

            # Create the worksheet first
            workbook = writer.book
            worksheet = workbook.add_worksheet(sheet_name)

            # Set up formats
            header_format = workbook.add_format(
                {"bold": True, "align": "center", "bg_color": "#EEECE1"}
            )
            section_format = workbook.add_format(
                {"bold": True, "align": "left", "bg_color": "#EEECE1", "border": 1, "font_size": 14}
            )
            regular_format = workbook.add_format({"border": 1, "align": "center"})

            # Add color formats for scores
            red_format = workbook.add_format(
                {"bg_color": "#FFC7CE", "align": "center"}
            )  # Light red
            orange_format = workbook.add_format(
                {"bg_color": "#FFEB9C", "align": "center"}
            )  # Light orange
            green_format = workbook.add_format(
                {"bg_color": "#C6EFCE", "align": "center"}
            )  # Light green
            empty_format = workbook.add_format(
                {"align": "center"}
            )  # No border for empty/zero scores

            # Get models list
            models = list(data["models"].keys())

            # Add Accuracy Metrics header and section first
            worksheet.set_row(0, 20)  # Set row height for section header
            worksheet.merge_range(0, 0, 0, 5, "Accuracy Metrics", section_format)

            # Headers for accuracy metrics
            worksheet.write(1, 0, "Model", header_format)
            worksheet.write(1, 1, "score_summary", header_format)
            worksheet.write(1, 2, "avg_chat_score", header_format)
            worksheet.write(1, 3, "score_vlm", header_format)
            worksheet.write(1, 4, "unique_test_code", header_format)
            worksheet.write(1, 5, "timestamp", header_format)

            # Accuracy data rows with color coding
            row_offset = 2
            for model_name, accuracy in data["accuracy_data"].items():
                worksheet.write(row_offset, 0, model_name)

                # Score summary with color coding
                score_summary = accuracy.get("score_summary", 0)
                if 1 <= score_summary <= 3:
                    format_to_use = red_format
                elif 4 <= score_summary < 7:
                    format_to_use = orange_format
                elif 7 <= score_summary <= 10:
                    format_to_use = green_format
                elif score_summary == 0:
                    format_to_use = empty_format
                else:
                    format_to_use = regular_format
                worksheet.write(row_offset, 1, score_summary, format_to_use)

                # Avg chat score with color coding
                avg_chat_score = accuracy.get("avg_chat_score", 0)
                if 1 <= avg_chat_score <= 3:
                    format_to_use = red_format
                elif 4 <= avg_chat_score < 7:
                    format_to_use = orange_format
                elif 7 <= avg_chat_score <= 10:
                    format_to_use = green_format
                elif avg_chat_score == 0:
                    format_to_use = empty_format
                else:
                    format_to_use = regular_format
                worksheet.write(row_offset, 2, round(avg_chat_score, 1), format_to_use)

                # VLM score with color coding
                vlm_score = accuracy.get("score_vlm", 0)
                if vlm_score is not None and vlm_score != 0 and vlm_score != "evaluate_dc error":
                    vlm_score_val = round(vlm_score, 1)
                    if 1 <= vlm_score_val <= 3:
                        format_to_use = red_format
                    elif 4 <= vlm_score_val < 7:
                        format_to_use = orange_format
                    elif 7 <= vlm_score_val <= 10:
                        format_to_use = green_format
                    else:
                        format_to_use = regular_format
                    worksheet.write(row_offset, 3, vlm_score_val, format_to_use)
                else:
                    worksheet.write(row_offset, 3, "", empty_format)

                worksheet.write(row_offset, 4, accuracy.get("unique_test_code", ""))
                worksheet.write(row_offset, 5, accuracy.get("timestamp", ""))
                row_offset += 1

            # Set Q&A column widths
            worksheet.set_column(0, 0, 30)  # question
            worksheet.set_column(1, 1, 30)  # expected answer

            col_idx = 2
            for model in models:
                worksheet.set_column(col_idx, col_idx, 30)  # response
                worksheet.set_column(col_idx + 1, col_idx + 1, 6)  # score

                col_idx += 2

            # Add 2 line buffer before Configuration section
            row_offset += 2

            # Video Configuration and VLM Configuration side by side
            worksheet.set_row(row_offset, 20)  # Set row height for section headers
            worksheet.merge_range(
                row_offset, 0, row_offset, 1, "Video Configuration", section_format
            )

            # Calculate VLM config merge range based on number of models
            vlm_start_col = 3
            vlm_end_col = 3 + len(data["models"]) if data["models"] else 4
            worksheet.merge_range(
                row_offset,
                vlm_start_col,
                row_offset,
                vlm_end_col,
                "VLM Configuration",
                section_format,
            )
            row_offset += 1

            # Video Configuration on the left (columns 0-6)
            config_data = data.get("config_data")
            video_config_rows = 0
            if config_data:
                # Define which fields to include based on config format
                if "video_id" in config_data:
                    # New format
                    config_fields = [
                        ("video_id", "Video ID"),
                        ("enable_cv", "Enable CV"),
                        ("enable_audio", "Enable Audio"),
                        ("chunk_size", "Chunk Size"),
                        ("caption_prompt", "Caption Prompt"),
                        ("caption_summarization_prompt", "Caption Summarization Prompt"),
                        ("summary_aggregation_prompt", "Summary Aggregation Prompt"),
                        ("summary_gt", "Summary Ground Truth"),
                        ("qa_gt", "QA Ground Truth"),
                        ("dc_gt", "DC Ground Truth"),
                    ]
                else:
                    # Old format
                    config_fields = [
                        ("VLM_MODEL_TO_USE", "VLM Model"),
                        ("CHUNK_SIZE", "Chunk Size"),
                        ("GROUND_TRUTH_FILE", "Ground Truth File"),
                        ("SUMMARY_GT", "Summary Ground Truth"),
                        ("PROMPT_1", "Caption Prompt"),
                        ("PROMPT_2", "Caption Summarization Prompt"),
                        ("PROMPT_3", "Summary Aggregation Prompt"),
                    ]

                # Headers for video configuration
                worksheet.write(row_offset, 0, "Parameter", header_format)
                worksheet.write(row_offset, 1, "Value", header_format)

                # Add video configuration rows
                config_row_start = row_offset + 1
                config_row_offset = config_row_start
                for field_key, field_display in config_fields:
                    if field_key in config_data:
                        worksheet.write(config_row_offset, 0, field_display)

                        value = config_data[field_key]

                        # Special handling for num_frames_per_chunk when null (only in new format)
                        if field_key == "num_frames_per_chunk" and value is None:
                            worksheet.write_url(
                                config_row_offset,
                                1,
                                (
                                    "https://docs.nvidia.com/vss/latest/content/"
                                    "vss_configuration.html#vlm-default-number-of-frames-per-chunk"
                                ),
                                string="default",
                            )

                        elif (
                            field_key == "caption_prompt"
                            or field_key == "caption_summarization_prompt"
                            or field_key == "summary_aggregation_prompt"
                        ):
                            # Don't truncate prompts - show full text
                            display_value = str(value) if value is not None else ""
                            # Merge across two cells for prompts
                            worksheet.merge_range(
                                config_row_offset, 1, config_row_offset, 2, display_value
                            )

                        else:
                            # Don't truncate prompts - show full text
                            display_value = str(value) if value is not None else ""
                            worksheet.write(config_row_offset, 1, display_value)

                        config_row_offset += 1

                video_config_rows = config_row_offset - config_row_start

                # Set column width for video configuration section
                worksheet.set_column(0, 0, 25)  # Parameter column
                worksheet.set_column(1, 1, 50)  # Value column (wider for full prompts)

            # VLM Configuration on the right (starting from column D/3)
            model_vlm_configs = {}
            for model_name in data["models"].keys():
                model_config_data = data["model_configs"].get(model_name)
                if model_config_data and isinstance(model_config_data, dict):
                    vlm_config = model_config_data.get("VLM_config")
                    if vlm_config:
                        model_vlm_configs[model_name] = vlm_config.copy()
                        # Also store CA RAG and Guardrail configs
                        model_vlm_configs[model_name]["_ca_rag_config"] = model_config_data.get(
                            "CA_RAG_CONFIG"
                        )
                        model_vlm_configs[model_name]["_guardrail_config"] = model_config_data.get(
                            "Guardrail_Configurations"
                        )

            vlm_config_rows = 0
            if model_vlm_configs:
                # Get list of models for header and create display names
                config_models = list(model_vlm_configs.keys())
                model_display_names = {}

                for model_name in config_models:
                    vlm_config = model_vlm_configs[model_name]
                    model_type = vlm_config.get("model", "")

                    if model_type == "custom":
                        # Use last part of model path with "custom: " prefix
                        model_path = vlm_config.get("model_path", "")
                        if model_path:
                            folder_name = model_path.split("/")[-1]
                            display_name = f"custom: {folder_name}"
                        else:
                            display_name = f"custom: {model_name}"
                    else:
                        # For all non-custom models, use the full model_name to preserve UUID suffixes
                        # This ensures that when there are duplicates like "gpt-4o - abc1" and "gpt-4o - def2"
                        # both show their UUIDs instead of losing the distinction
                        display_name = model_name

                    model_display_names[model_name] = display_name

                # Headers for VLM configuration
                worksheet.write(row_offset, 3, "Parameter", header_format)
                for col_idx, model_name in enumerate(config_models):
                    display_name = model_display_names[model_name]
                    worksheet.write(row_offset, 4 + col_idx, display_name, header_format)

                # Define VLM config fields to display
                vlm_config_fields = [
                    ("model", "Model"),
                    ("model_path", "Model Path"),
                    ("VLM_batch_size", "VLM Batch Size"),
                    ("frames_per_chunk", "Frames per Chunk"),
                    ("VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME", "OpenAI Model Name"),
                    ("VIA_VLM_ENDPOINT", "VLM Endpoint"),
                    ("AZURE_OPENAI_ENDPOINT", "Azure OpenAI Endpoint"),
                    ("_ca_rag_config", "CA RAG Config"),
                    ("_guardrail_enabled", "Guardrails Enabled"),
                    ("_guardrail_config_file", "Guardrail Config File"),
                ]

                # Add rows for each VLM config parameter
                vlm_row_offset = row_offset + 1
                for field_key, field_display in vlm_config_fields:
                    # Check if any model has this field
                    has_field = False
                    row_values = {}

                    for model_name in config_models:
                        vlm_config = model_vlm_configs[model_name]
                        value = None

                        if field_key == "_guardrail_enabled":
                            guardrail_config = vlm_config.get("_guardrail_config")
                            if guardrail_config and isinstance(guardrail_config, dict):
                                value = "Yes" if guardrail_config.get("enable") else "No"
                                if value != "No":
                                    has_field = True
                        elif field_key == "_guardrail_config_file":
                            guardrail_config = vlm_config.get("_guardrail_config")
                            if guardrail_config and isinstance(guardrail_config, dict):
                                value = guardrail_config.get("guardrail_config_file")
                                if value:
                                    has_field = True
                        elif field_key == "_ca_rag_config":
                            value = vlm_config.get("_ca_rag_config")
                            if value:
                                has_field = True
                        elif field_key in vlm_config:
                            value = vlm_config[field_key]

                            # Special handling for VLM_batch_size - only show if model is nvila
                            if field_key == "VLM_batch_size":
                                model_type = vlm_config.get("model", "")
                                if model_type == "nvila":
                                    has_field = True
                                    if value is None:
                                        value = "default"
                                else:
                                    value = "n/a"
                            # Only show endpoint fields if they have values
                            elif field_key in [
                                "VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME",
                                "VIA_VLM_ENDPOINT",
                                "AZURE_OPENAI_ENDPOINT",
                            ]:
                                if value and str(value).strip():
                                    has_field = True
                            else:
                                has_field = True

                        row_values[model_name] = value

                    # Only add row if at least one model has this field
                    if has_field:
                        worksheet.write(vlm_row_offset, 3, field_display)

                        for col_idx, model_name in enumerate(config_models):
                            value = row_values.get(model_name)

                            # Special handling for frames_per_chunk when null
                            if field_key == "frames_per_chunk" and value is None:
                                worksheet.write_url(
                                    vlm_row_offset,
                                    4 + col_idx,
                                    (
                                        "https://docs.nvidia.com/vss/latest/content/"
                                        "vss_configuration.html#vlm-default-number-of-frames-per-chunk"
                                    ),
                                    string="default",
                                )
                            else:
                                display_value = str(value) if value is not None else ""
                                worksheet.write(vlm_row_offset, 4 + col_idx, display_value)

                        vlm_row_offset += 1

                vlm_config_rows = vlm_row_offset - (row_offset + 1)

                # Set column widths for VLM config section
                worksheet.set_column(3, 3, 25)  # Parameter column
                for col_idx in range(len(config_models)):
                    worksheet.set_column(4 + col_idx, 4 + col_idx, 20)  # Model columns (wider)

            # Set row_offset to after both configuration sections
            row_offset = row_offset + 1 + max(video_config_rows, vlm_config_rows)

            # Add Q&A Evaluation section
            row_offset += 2
            worksheet.set_row(row_offset, 20)  # Set row height for section header
            qa_section_end_col = 1 + (len(models) * 2)
            worksheet.merge_range(
                row_offset, 0, row_offset, qa_section_end_col, "Q&A Evaluation", section_format
            )
            row_offset += 1

            # Write data to Excel without headers
            result_df.to_excel(
                writer, sheet_name=sheet_name, index=False, header=False, startrow=row_offset + 2
            )

            # Write Q&A headers
            worksheet.set_row(row_offset, 20)  # Set row height for headers
            worksheet.write(row_offset, 0, "Question", header_format)
            worksheet.write(row_offset, 1, "Expected Answer", header_format)

            col_offset = 2  # Start after question and expected answer
            for model in models:
                worksheet.merge_range(
                    row_offset, col_offset, row_offset, col_offset + 1, model, header_format
                )
                col_offset += 2

            # Write the response and score headers
            worksheet.set_row(row_offset + 1, 18)  # Set row height for sub-headers
            worksheet.write(row_offset + 1, 0, "", header_format)  # Empty cell for question
            worksheet.write(row_offset + 1, 1, "", header_format)  # Empty cell for expected answer

            col_offset = 2  # Reset
            for _ in models:
                worksheet.write(row_offset + 1, col_offset, "Response", header_format)
                worksheet.write(row_offset + 1, col_offset + 1, "Score", header_format)
                col_offset += 2

            row_offset = row_offset + 2 + len(result_df)

            # Add conditional formatting for Q&A data (after data is written)
            qa_data_start_row = row_offset - len(result_df)  # Start of actual Q&A data
            qa_data_end_row = row_offset - 1  # End of actual Q&A data

            col_idx = 2
            for model in models:
                response_col = col_idx
                score_col = col_idx + 1

                # RED formatting (score >= 1 && <= 3)
                worksheet.conditional_format(
                    qa_data_start_row,
                    response_col,
                    qa_data_end_row,
                    response_col,
                    {
                        "type": "formula",
                        "criteria": (
                            f"=AND(${chr(65 + score_col)}{qa_data_start_row+1}>=1,"
                            f"${chr(65 + score_col)}{qa_data_start_row+1}<=3)"
                        ),
                        "format": red_format,
                    },
                )
                worksheet.conditional_format(
                    qa_data_start_row,
                    score_col,
                    qa_data_end_row,
                    score_col,
                    {
                        "type": "cell",
                        "criteria": "between",
                        "minimum": 1,
                        "maximum": 3,
                        "format": red_format,
                    },
                )

                # ORANGE formatting (score > 3 && < 7)
                worksheet.conditional_format(
                    qa_data_start_row,
                    response_col,
                    qa_data_end_row,
                    response_col,
                    {
                        "type": "formula",
                        "criteria": (
                            f"=AND(${chr(65 + score_col)}{qa_data_start_row+1}>3,"
                            f"${chr(65 + score_col)}{qa_data_start_row+1}<7)"
                        ),
                        "format": orange_format,
                    },
                )
                worksheet.conditional_format(
                    qa_data_start_row,
                    score_col,
                    qa_data_end_row,
                    score_col,
                    {
                        "type": "cell",
                        "criteria": "between",
                        "minimum": 4,
                        "maximum": 6.9999,
                        "format": orange_format,
                    },
                )

                # GREEN formatting (score >= 7 && <= 10)
                worksheet.conditional_format(
                    qa_data_start_row,
                    response_col,
                    qa_data_end_row,
                    response_col,
                    {
                        "type": "formula",
                        "criteria": f"=AND(${chr(65 + score_col)}{qa_data_start_row+1}>=7,${chr(65 + score_col)}{qa_data_start_row+1}<=10)",  # noqa: E501
                        "format": green_format,
                    },
                )
                worksheet.conditional_format(
                    qa_data_start_row,
                    score_col,
                    qa_data_end_row,
                    score_col,
                    {
                        "type": "cell",
                        "criteria": "between",
                        "minimum": 7,
                        "maximum": 10,
                        "format": green_format,
                    },
                )

                col_idx += 2

            # Add 1 line buffer before Dense Caption Evaluation
            row_offset += 1

            # Add Dense Caption Evaluation data if available
            if data["dc_eval"] is not None and data["dc_eval"]["models"]:
                dc_section_end_col = 1 + (len(data["dc_eval"]["models"]) * 2)
                worksheet.merge_range(
                    row_offset,
                    0,
                    row_offset,
                    dc_section_end_col,
                    "Dense Caption Evaluation",
                    section_format,
                )
                worksheet.set_row(row_offset, 20)  # Set row height for section header
                row_offset += 1

                # Parse DC data
                try:
                    # Parse ground truth data (detect format)
                    gt_content = data["dc_eval"]["ground_truth"]
                    gt_lines = [line.strip() for line in gt_content.split("\n") if line.strip()]
                    gt_chunks = {}

                    # Detect and parse ground truth format
                    if gt_lines and (
                        gt_lines[0].startswith("Chunk_ID")
                        or ("," in gt_lines[0] and not gt_lines[0].startswith("{"))
                    ):
                        # CSV format: Chunk_ID,Expected Answer
                        lines_to_parse = (
                            gt_lines[1:] if gt_lines[0].startswith("Chunk_ID") else gt_lines
                        )
                        for line in lines_to_parse:
                            if "," in line:
                                first_comma = line.find(",")
                                if first_comma > 0:
                                    chunk_id_str = line[:first_comma].strip()
                                    answer = line[first_comma + 1 :].strip()
                                    if answer.startswith('"') and answer.endswith('"'):
                                        answer = answer[1:-1]
                                    try:
                                        chunk_id = int(chunk_id_str)
                                    except ValueError:
                                        chunk_id = chunk_id_str
                                    gt_chunks[chunk_id] = answer

                    elif gt_lines and gt_lines[0].startswith("{"):
                        # Single JSON object format: {"0": "caption", "1": "caption", ...}
                        try:
                            full_json_str = "\n".join(gt_lines)
                            gt_json = json.loads(full_json_str)
                            if isinstance(gt_json, dict):
                                for chunk_id_str, caption in gt_json.items():
                                    try:
                                        chunk_id = int(chunk_id_str)
                                    except ValueError:
                                        chunk_id = chunk_id_str
                                    gt_chunks[chunk_id] = caption
                        except json.JSONDecodeError as e:
                            print(f"Error parsing GT JSON object for {video_name}: {e}")
                            # Fall back to line-by-line parsing
                            for line in gt_lines:
                                try:
                                    gt_json = json.loads(line)
                                    if "chunk" in gt_json and "chunkIdx" in gt_json["chunk"]:
                                        chunk_id = gt_json["chunk"]["chunkIdx"]
                                        if "vlm_response" in gt_json:
                                            gt_chunks[chunk_id] = gt_json["vlm_response"]
                                        elif "expected_answer" in gt_json:
                                            gt_chunks[chunk_id] = gt_json["expected_answer"]
                                        elif "answer" in gt_json:
                                            gt_chunks[chunk_id] = gt_json["answer"]
                                except json.JSONDecodeError:
                                    continue

                    else:
                        # JSONL format: JSON Lines
                        for line in gt_lines:
                            try:
                                gt_json = json.loads(line)
                                if "chunk" in gt_json and "chunkIdx" in gt_json["chunk"]:
                                    chunk_id = gt_json["chunk"]["chunkIdx"]
                                    if "vlm_response" in gt_json:
                                        gt_chunks[chunk_id] = gt_json["vlm_response"]
                                    elif "expected_answer" in gt_json:
                                        gt_chunks[chunk_id] = gt_json["expected_answer"]
                                    elif "answer" in gt_json:
                                        gt_chunks[chunk_id] = gt_json["answer"]
                            except json.JSONDecodeError:
                                continue

                    # Parse scores from dc_scores.csv - per model (like responses)
                    scores_per_model_parsed = {}
                    scores_per_model = data["dc_eval"].get("scores_per_model", {})

                    # Parse scores for each model separately
                    for model_name, model_scores_content in scores_per_model.items():
                        model_scores = {}
                        if model_scores_content and model_scores_content.strip():
                            scores_lines = [
                                line.strip()
                                for line in model_scores_content.split("\n")
                                if line.strip()
                            ]

                            data_lines = (
                                len(scores_lines) - 1
                                if scores_lines and scores_lines[0].startswith("Chunk_ID")
                                else len(scores_lines)
                            )

                            if (
                                scores_lines
                                and scores_lines[0].startswith("Chunk_ID")
                                and data_lines > 0
                            ):
                                for line in scores_lines[1:]:
                                    parts = line.split(
                                        ",", 2
                                    )  # Split into 3 parts max (ID, Score, Reasoning)
                                    if len(parts) >= 2:
                                        try:
                                            chunk_id_str = parts[0].strip()
                                            score_str = parts[1].strip()
                                            score = float(score_str)
                                            # Convert to same type as model chunks for consistency
                                            try:
                                                chunk_id = int(chunk_id_str)
                                            except ValueError:
                                                chunk_id = chunk_id_str
                                            model_scores[chunk_id] = score
                                        except ValueError:
                                            continue

                        scores_per_model_parsed[model_name] = model_scores

                    # Parse model responses (CSV format)
                    model_responses = {}
                    dc_models = list(data["dc_eval"]["models"].keys())

                    for model_name, content in data["dc_eval"]["models"].items():
                        model_chunks = {}
                        lines = [line.strip() for line in content.split("\n") if line.strip()]

                        if lines and lines[0].startswith("Chunk_ID"):
                            lines = lines[1:]

                        for line in lines:
                            if "," in line:
                                first_comma = line.find(",")
                                if first_comma > 0:
                                    chunk_id_str = line[:first_comma].strip()
                                    answer = line[first_comma + 1 :].strip()
                                    if answer.startswith('"') and answer.endswith('"'):
                                        answer = answer[1:-1]
                                    try:
                                        chunk_id = int(chunk_id_str)
                                    except ValueError:
                                        chunk_id = chunk_id_str
                                    model_chunks[chunk_id] = answer

                        model_responses[model_name] = model_chunks

                    # Step 1: Get all chunk IDs from vlm_testdata (this drives what we display)
                    vlm_chunks = {}
                    for model_chunks in model_responses.values():
                        vlm_chunks.update(model_chunks)

                    # Get sorted list of chunk IDs from VLM responses
                    chunk_ids = sorted(vlm_chunks.keys())

                    # Step 2: Create ground truth dictionary based on vlm chunk IDs
                    gt_for_vlm_chunks = {}
                    for chunk_id in chunk_ids:
                        gt_for_vlm_chunks[chunk_id] = gt_chunks.get(chunk_id, "")

                    # Step 3: Create response dictionaries for each model based on vlm chunk IDs
                    model_responses_for_vlm_chunks = {}
                    for model_name in dc_models:
                        model_responses_for_vlm_chunks[model_name] = {}
                        for chunk_id in chunk_ids:
                            model_responses_for_vlm_chunks[model_name][chunk_id] = (
                                model_responses.get(model_name, {}).get(chunk_id, "")
                            )

                    # Step 4: Create scores dictionaries for each model based on vlm chunk IDs
                    scores_per_model_for_vlm_chunks = {}
                    for model_name in dc_models:
                        scores_per_model_for_vlm_chunks[model_name] = {}
                        model_scores = scores_per_model_parsed.get(model_name, {})
                        for chunk_id in chunk_ids:
                            scores_per_model_for_vlm_chunks[model_name][chunk_id] = (
                                model_scores.get(chunk_id, "")
                            )

                    if chunk_ids:
                        # Create headers for comprehensive DC evaluation table (matching Q&A format)
                        # First header row: Chunk ID, Ground Truth, and model names spanning 2 columns each
                        worksheet.write(row_offset, 0, "Chunk ID", header_format)
                        worksheet.write(row_offset, 1, "Ground Truth", header_format)

                        col_offset = 2
                        for model_name in dc_models:
                            worksheet.merge_range(
                                row_offset,
                                col_offset,
                                row_offset,
                                col_offset + 1,
                                model_name,
                                header_format,
                            )
                            col_offset += 2
                        row_offset += 1

                        # Second header row: empty cells under Chunk ID and Ground Truth,
                        # then response/score headers
                        worksheet.write(
                            row_offset, 0, "", header_format
                        )  # Empty cell under Chunk ID
                        worksheet.write(
                            row_offset, 1, "", header_format
                        )  # Empty cell under Ground Truth

                        col_offset = 2
                        for _ in dc_models:
                            worksheet.write(row_offset, col_offset, "response", header_format)
                            worksheet.write(row_offset, col_offset + 1, "score", header_format)
                            col_offset += 2
                        row_offset += 1

                        # Set column widths
                        # worksheet.set_column(0, 0, 10)   # Chunk ID
                        worksheet.set_column(1, 1, 50)  # Ground Truth
                        col_index = 2
                        for i, model_name in enumerate(dc_models):
                            worksheet.set_column(col_index, col_index, 50)  # Model response
                            worksheet.set_column(col_index + 1, col_index + 1, 8)  # Model score
                            col_index += 2

                        # Add center alignment format for chunk IDs
                        center_format = workbook.add_format({"align": "center"})

                        # Add data rows - write all vlm chunk IDs with their corresponding data
                        start_data_row = row_offset
                        for chunk_id in chunk_ids:
                            # Chunk ID (centered)
                            worksheet.write(row_offset, 0, chunk_id, center_format)

                            # Ground truth based on vlm chunk IDs
                            gt_text = gt_for_vlm_chunks[chunk_id]
                            worksheet.write(row_offset, 1, gt_text)

                            # Model responses and scores for each model
                            col_index = 2
                            for model_name in dc_models:
                                # Model response for this vlm chunk ID
                                model_text = model_responses_for_vlm_chunks[model_name][chunk_id]
                                worksheet.write(row_offset, col_index, model_text)

                                # Score for this chunk from THIS model's dc_scores.csv
                                chunk_score = scores_per_model_for_vlm_chunks[model_name][chunk_id]
                                worksheet.write(row_offset, col_index + 1, chunk_score)
                                col_index += 2

                            row_offset += 1

                        # Add conditional formatting for DC evaluation (matching Q&A format)
                        end_data_row = row_offset - 1

                        # Apply color coding to both response and score columns based on score value
                        col_idx = 2
                        for model_name in dc_models:
                            response_col = col_idx
                            score_col = col_idx + 1

                            # RED formatting (score 1-3) - apply to both response and score columns
                            worksheet.conditional_format(
                                start_data_row,
                                response_col,
                                end_data_row,
                                response_col,
                                {
                                    "type": "formula",
                                    "criteria": (
                                        f"=AND(${chr(65 + score_col)}{start_data_row+1}>=1,"
                                        f"${chr(65 + score_col)}{start_data_row+1}<=3)"
                                    ),
                                    "format": red_format,
                                },
                            )
                            worksheet.conditional_format(
                                start_data_row,
                                score_col,
                                end_data_row,
                                score_col,
                                {
                                    "type": "cell",
                                    "criteria": "between",
                                    "minimum": 1,
                                    "maximum": 3,
                                    "format": red_format,
                                },
                            )

                            # ORANGE formatting (score >3 && <7)
                            worksheet.conditional_format(
                                start_data_row,
                                response_col,
                                end_data_row,
                                response_col,
                                {
                                    "type": "formula",
                                    "criteria": (
                                        f"=AND(${chr(65 + score_col)}{start_data_row+1}>3,"
                                        f"${chr(65 + score_col)}{start_data_row+1}<7)"
                                    ),
                                    "format": orange_format,
                                },
                            )
                            worksheet.conditional_format(
                                start_data_row,
                                score_col,
                                end_data_row,
                                score_col,
                                {
                                    "type": "cell",
                                    "criteria": "between",
                                    "minimum": 4,
                                    "maximum": 6.9999,
                                    "format": orange_format,
                                },
                            )

                            # GREEN formatting (score 7-10)
                            worksheet.conditional_format(
                                start_data_row,
                                response_col,
                                end_data_row,
                                response_col,
                                {
                                    "type": "formula",
                                    "criteria": (
                                        f"=AND(${chr(65 + score_col)}{start_data_row+1}>=7,"
                                        f"${chr(65 + score_col)}{start_data_row+1}<=10)"
                                    ),
                                    "format": green_format,
                                },
                            )
                            worksheet.conditional_format(
                                start_data_row,
                                score_col,
                                end_data_row,
                                score_col,
                                {
                                    "type": "cell",
                                    "criteria": "between",
                                    "minimum": 7,
                                    "maximum": 10,
                                    "format": green_format,
                                },
                            )

                            col_idx += 2

                except Exception as e:
                    print(f"Error processing DC evaluation data: {e}")
                    worksheet.write(row_offset, 0, f"Error processing DC data: {e}")
                    row_offset += 1


def main():
    if len(sys.argv) != 4:
        print(
            "Usage: python get_summary_qa_results_into_xlsx.py "
            "<logs_folder_path> <byov_config_file> <output_file_name>"
        )
        sys.exit(1)

    base_path = sys.argv[1]
    byov_config_file = sys.argv[2]
    output_path = sys.argv[3]
    video_data = process_results(base_path)
    create_excel_report(video_data, output_path, byov_config_file)
    print(f"Report created: {output_path}")


if __name__ == "__main__":
    main()
