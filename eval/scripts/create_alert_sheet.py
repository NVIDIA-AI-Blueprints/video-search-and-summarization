######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

from typing import Any, Dict, List
import glob
import json
import os

import xlsxwriter


def flatten_json(data, parent_key='', sep='_'):
    """
    Flatten a nested JSON structure into a flat dictionary.
    
    Args:
        data: JSON data (dict, list, or primitive)
        parent_key: Key prefix for nested items
        sep: Separator between nested keys
        
    Returns:
        dict: Flattened dictionary
    """
    items = []
    
    if isinstance(data, dict):
        for k, v in data.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.extend(flatten_json(v, new_key, sep=sep).items())
            elif isinstance(v, list):
                # Handle lists
                if v and isinstance(v[0], dict):
                    # List of dicts - only take first item for summary
                    items.extend(flatten_json(v[0], f"{new_key}_0", sep=sep).items())
                    items.append((f"{new_key}_count", len(v)))
                else:
                    # List of primitives - join with comma
                    items.append((new_key, ", ".join(str(x) for x in v)))
            else:
                items.append((new_key, v))
    elif isinstance(data, list):
        # Top-level list
        for i, item in enumerate(data):
            items.extend(flatten_json(item, f"{parent_key}{sep}{i}" if parent_key else str(i), sep=sep).items())
    else:
        # Primitive value
        items.append((parent_key, data))
    
    return dict(items)


def collect_health_eval_data(log_base_dir="logs/accuracy/alert"):
    """
    Collect health eval data from all test case log directories.
    
    Args:
        log_base_dir: Base directory containing alert logs
        
    Returns:
        list: List of dicts containing flattened health eval data with test_case_id and config_name
    """
    health_data = []
    
    if not os.path.exists(log_base_dir):
        print(f"Health eval log directory not found: {log_base_dir}")
        return health_data
    
    # Walk through all config directories
    for config_dir in glob.glob(os.path.join(log_base_dir, "*")):
        if not os.path.isdir(config_dir):
            continue
            
        config_name = os.path.basename(config_dir)
        
        # Walk through all test case directories
        for test_case_dir in glob.glob(os.path.join(config_dir, "*")):
            if not os.path.isdir(test_case_dir):
                continue
                
            test_case_id = os.path.basename(test_case_dir)
            
            # Find all health summary JSON files
            health_files = glob.glob(os.path.join(test_case_dir, "via_health_summary_*.json"))
            
            for health_file in health_files:
                try:
                    with open(health_file, 'r') as f:
                        health_json = json.load(f)
                    
                    # Flatten the JSON structure
                    flat_data = flatten_json(health_json)
                    
                    # Add identifiers
                    flat_data['test_case_id'] = test_case_id
                    flat_data['config_name'] = config_name
                    flat_data['health_file'] = os.path.basename(health_file)
                    
                    health_data.append(flat_data)
                    
                except Exception as e:
                    print(f"Error reading health file {health_file}: {e}")
    
    return health_data


class AccuracyMetrics:
    """
    Data class for calculating and storing classification accuracy metrics.

    Provides methods to compute precision, recall, F1-score, and overall
    accuracy from confusion matrix values.
    """

    def __init__(self, results=None):
        """Initialize AccuracyMetrics, optionally from a list of results."""
        self.true_positives = 0
        self.false_positives = 0
        self.true_negatives = 0
        self.false_negatives = 0

        if results:
            for result in results:
                if result["generated_answer"] == result["ground_truth"]:
                    if result["generated_answer"] is True:
                        self.true_positives += 1  # Correctly predicted positive
                    else:
                        self.true_negatives += 1  # Correctly predicted negative
                elif result["generated_answer"] is True and result["ground_truth"] is False:
                    self.false_positives += 1  # Predicted positive, but was negative
                elif result["generated_answer"] is False and result["ground_truth"] is True:
                    self.false_negatives += 1  # Predicted negative, but was positive

    def precision(self):
        """Calculate precision: TP / (TP + FP)"""
        if (self.true_positives + self.false_positives) == 0:
            return 0.0
        return self.true_positives / (self.true_positives + self.false_positives)

    def recall(self):
        """Calculate recall: TP / (TP + FN)"""
        if (self.true_positives + self.false_negatives) == 0:
            return 0.0
        return self.true_positives / (self.true_positives + self.false_negatives)

    def f1_score(self):
        """Calculate F1 score: 2 * (precision * recall) / (precision + recall)"""
        if (self.precision() + self.recall()) == 0:
            return 0.0
        return 2 * (self.precision() * self.recall()) / (self.precision() + self.recall())

    def accuracy(self):
        """Calculate accuracy: (TP + TN) / (TP + FP + TN + FN)"""
        total = (
            self.true_positives + self.false_positives + self.true_negatives + self.false_negatives
        )
        if total == 0:
            return 0.0
        return (self.true_positives + self.true_negatives) / total


def create_health_eval_sheet(workbook, health_data, header_format, normal_format):
    """
    Create a Health Eval sheet with data from health summary JSON files.
    
    Args:
        workbook: xlsxwriter Workbook object
        health_data: List of flattened health eval dictionaries
        header_format: Format for headers
        normal_format: Format for normal cells
    """
    worksheet = workbook.add_worksheet("Health Eval")
    
    if not health_data:
        worksheet.write(0, 0, "No health eval data available", normal_format)
        return
    
    # Get all unique keys across all records (for dynamic columns)
    all_keys = set()
    for record in health_data:
        all_keys.update(record.keys())
    
    # Define column order: identifiers first, then alphabetically sorted metrics
    priority_columns = ['test_case_id', 'config_name', 'health_file', 'vlm_model_name']
    other_columns = sorted([k for k in all_keys if k not in priority_columns])
    column_order = priority_columns + other_columns
    
    # Write headers
    for col_idx, key in enumerate(column_order):
        worksheet.write(0, col_idx, key, header_format)
        # Auto-size columns based on header length (minimum 12, maximum 40)
        col_width = max(12, min(40, len(str(key)) + 2))
        worksheet.set_column(col_idx, col_idx, col_width)
    
    # Write data rows
    for row_idx, record in enumerate(health_data, start=1):
        for col_idx, key in enumerate(column_order):
            value = record.get(key, "")
            
            # Format numeric values
            if isinstance(value, (int, float)):
                if isinstance(value, float):
                    # Format floats with appropriate precision
                    if abs(value) < 0.01 and value != 0:
                        worksheet.write(row_idx, col_idx, f"{value:.6f}", normal_format)
                    else:
                        worksheet.write(row_idx, col_idx, f"{value:.4f}", normal_format)
                else:
                    worksheet.write(row_idx, col_idx, value, normal_format)
            else:
                # Write strings and other types
                worksheet.write(row_idx, col_idx, str(value), normal_format)
    
    print(f"Created Health Eval sheet with {len(health_data)} records and {len(column_order)} columns")


def generate_results_summary(
    all_results: Dict[str, Dict[str, List[Dict[str, Any]]]],
    configs_info: Dict[str, Dict] = None,
    output_file: str = "alert_eval_results.xlsx",
):
    """Generate a comprehensive Excel spreadsheet with results summary and detailed analysis"""
    print("\n" + "=" * 80)
    print("ALERT VERIFICATION PERFORMANCE AND CLASSIFICATION METRICS SUMMARY")
    print("=" * 80)

    # Create workbook and define formats
    workbook = xlsxwriter.Workbook(output_file)

    # Define cell formats
    header_format = workbook.add_format(
        {
            "bold": True,
            "bg_color": "#D3D3D3",
            "border": 1,
            "text_wrap": True,
            "valign": "vcenter",
            "align": "center",
        }
    )

    normal_format = workbook.add_format({"border": 1, "text_wrap": True, "valign": "top"})
    normal_format_compact = workbook.add_format(
        {"border": 1, "valign": "top", "text_wrap": True}
    )  # With wrap for taller rows

    # Formats for results tables with text wrap (since we have taller rows now)
    match_format_compact = workbook.add_format(
        {
            "bg_color": "#C6EFCE",
            "border": 1,
            "valign": "top",
            "text_wrap": True,
        }  # Light green with wrap
    )

    mismatch_format_compact = workbook.add_format(
        {
            "bg_color": "#FFC7CE",
            "border": 1,
            "valign": "top",
            "text_wrap": True,
        }  # Light red with wrap
    )

    # Score-based color formats for VLM Chat results with text wrap
    high_score_format = workbook.add_format(
        {
            "bg_color": "#C6EFCE",
            "border": 1,
            "valign": "top",
            "text_wrap": True,
        }  # Light green (8-10) with wrap
    )

    medium_score_format = workbook.add_format(
        {
            "bg_color": "#FFEB9C",
            "border": 1,
            "valign": "top",
            "text_wrap": True,
        }  # Light yellow (5-7) with wrap
    )

    low_score_format = workbook.add_format(
        {
            "bg_color": "#FFC7CE",
            "border": 1,
            "valign": "top",
            "text_wrap": True,
        }  # Light red (1-4) with wrap
    )

    # Create colored formats for metrics (moved outside loop to avoid closure issues)
    metric_high_format = workbook.add_format(
        {"bg_color": "#C6EFCE", "num_format": "0.000", "border": 1, "align": "center"}
    )
    metric_medium_format = workbook.add_format(
        {"bg_color": "#FFEB9C", "num_format": "0.000", "border": 1, "align": "center"}
    )
    metric_low_format = workbook.add_format(
        {"bg_color": "#FFC7CE", "num_format": "0.000", "border": 1, "align": "center"}
    )

    score_high_format = workbook.add_format(
        {"bg_color": "#C6EFCE", "num_format": "0.00", "border": 1, "align": "center"}
    )
    score_medium_format = workbook.add_format(
        {"bg_color": "#FFEB9C", "num_format": "0.00", "border": 1, "align": "center"}
    )
    score_low_format = workbook.add_format(
        {"bg_color": "#FFC7CE", "num_format": "0.00", "border": 1, "align": "center"}
    )

    # Create summary worksheet
    summary_ws = workbook.add_worksheet("Summary")

    # Summary page headers
    headers = [
        "Configuration",
        "Num Clips",
        "Num Chat Queries",
        "Chat Avg Score",
        "Num Alert Queries",
        "Num Alert Clips",
        "Accuracy",
        "Precision",
        "Recall",
        "F1 Score",
        "TP",
        "FP",
        "TN",
        "FN",
    ]

    for col, header in enumerate(headers):
        summary_ws.write(0, col, header, header_format)
        summary_ws.set_column(col, col, 15)

    # Populate summary data
    summary_row = 1
    for config_name, config_results in all_results.items():
        # Extract alert results from the new format
        results = config_results.get("alerts", [])
        chat_results = config_results.get("vlm_chat", [])

        if not results:
            continue

        # Extract accuracy metrics from the configuration level (already calculated)
        num_clips = len(results)
        num_chat_prompts = len(chat_results)
        num_alert_queries = len(results)
        num_alert_clips = len(results)

        # Use the accuracy metrics stored at the configuration level
        total_accuracy_metrics = config_results.get("accuracy_metrics", AccuracyMetrics())

        # Calculate average chat score
        chat_avg_score = 0.0
        if chat_results:
            valid_scores = [
                float(cr.get("chat_score", 0))
                for cr in chat_results
                if cr.get("chat_score") is not None
            ]
            if valid_scores:
                chat_avg_score = sum(valid_scores) / len(valid_scores)

        # Determine color format for summary row based on overall performance
        accuracy = total_accuracy_metrics.accuracy()
        precision = total_accuracy_metrics.precision()
        recall = total_accuracy_metrics.recall()
        f1 = total_accuracy_metrics.f1_score()

        # Use score format for chat average (no color if score is 0)
        if chat_avg_score == 0:
            score_format_to_use = normal_format
        else:
            score_format_to_use = (
                score_high_format
                if chat_avg_score >= 8.0
                else score_medium_format if chat_avg_score >= 5.0 else score_low_format
            )

        # Write summary row with color coding (matching header order)
        summary_ws.write(summary_row, 0, config_name, normal_format)  # Configuration
        summary_ws.write(summary_row, 1, num_clips, normal_format)  # Num Clips
        summary_ws.write(summary_row, 2, num_chat_prompts, normal_format)  # Num Chat Queries
        summary_ws.write(summary_row, 3, chat_avg_score, score_format_to_use)  # Chat Avg Score
        summary_ws.write(summary_row, 4, num_alert_queries, normal_format)  # Num Alert Queries
        summary_ws.write(summary_row, 5, num_alert_clips, normal_format)  # Num Alert Clips

        # Helper function to get individual metric format based on value
        def get_individual_metric_format(value):
            if value == 0:
                return normal_format
            scaled_value = value * 10  # Convert 0-1 scale to 0-10 for threshold comparison
            if scaled_value >= 8.0:
                return metric_high_format
            elif scaled_value >= 5.0:
                return metric_medium_format
            else:
                return metric_low_format

        # Write each metric with its own individual color (continuing header order)
        summary_ws.write(
            summary_row, 6, accuracy, get_individual_metric_format(accuracy)
        )  # Accuracy
        summary_ws.write(
            summary_row, 7, precision, get_individual_metric_format(precision)
        )  # Precision
        summary_ws.write(summary_row, 8, recall, get_individual_metric_format(recall))  # Recall
        summary_ws.write(summary_row, 9, f1, get_individual_metric_format(f1))  # F1 Score

        summary_ws.write(
            summary_row, 10, total_accuracy_metrics.true_positives, normal_format
        )  # TP
        summary_ws.write(
            summary_row, 11, total_accuracy_metrics.false_positives, normal_format
        )  # FP
        summary_ws.write(
            summary_row, 12, total_accuracy_metrics.true_negatives, normal_format
        )  # TN
        summary_ws.write(
            summary_row, 13, total_accuracy_metrics.false_negatives, normal_format
        )  # FN

        summary_row += 1

    # Create detailed worksheets for each configuration
    for config_name, config_results in all_results.items():
        # Extract alert results from the new format
        results = config_results.get("alerts", [])
        chat_results = config_results.get("vlm_chat", [])

        if not results:
            continue

        # Create worksheet for this configuration
        worksheet = workbook.add_worksheet(config_name[:31])  # Excel sheet name limit

        # Set column widths for vertical layout
        worksheet.set_column(0, 0, 15)  # Column A - Labels/Test Case ID
        worksheet.set_column(1, 1, 40)  # Column B - Values/Prompt
        worksheet.set_column(2, 2, 15)  # Column C - Ground Truth
        worksheet.set_column(3, 3, 25)  # Column D - Generated Answer (widened)
        worksheet.set_column(4, 4, 10)  # Column E - Match/Score

        row_index = 0

        # Extract confusion matrix metrics from configuration level (already calculated)
        confusion_matrix = config_results.get("accuracy_metrics", AccuracyMetrics())

        # Write confusion matrix section
        worksheet.write(row_index, 0, "Confusion Matrix", header_format)
        worksheet.merge_range(row_index, 0, row_index, 2, "Confusion Matrix", header_format)
        row_index += 1

        # Confusion matrix headers
        worksheet.write(row_index, 1, "Predicted Positive", header_format)
        worksheet.write(row_index, 2, "Predicted Negative", header_format)
        row_index += 1

        worksheet.write(row_index, 0, "Actual Positive", header_format)
        worksheet.write(row_index, 1, f"TP: {confusion_matrix.true_positives}", normal_format)
        worksheet.write(row_index, 2, f"FN: {confusion_matrix.false_negatives}", normal_format)
        row_index += 1

        worksheet.write(row_index, 0, "Actual Negative", header_format)
        worksheet.write(row_index, 1, f"FP: {confusion_matrix.false_positives}", normal_format)
        worksheet.write(row_index, 2, f"TN: {confusion_matrix.true_negatives}", normal_format)
        row_index += 2

        # Metrics summary
        worksheet.write(row_index, 0, "Metrics Summary", header_format)
        worksheet.merge_range(row_index, 0, row_index, 1, "Metrics Summary", header_format)
        row_index += 1

        # Calculate metrics and determine color formats
        accuracy = confusion_matrix.accuracy()
        precision = confusion_matrix.precision()
        recall = confusion_matrix.recall()
        f1 = confusion_matrix.f1_score()

        # Use existing format variables based on performance thresholds (no color if value is 0)
        def get_metric_format_to_use(value):
            if value == 0:
                return normal_format
            scaled_value = value * 10  # Convert 0-1 scale to 0-10 for threshold comparison
            if scaled_value >= 8.0:
                return metric_high_format
            elif scaled_value >= 5.0:
                return metric_medium_format
            else:
                return metric_low_format

        worksheet.write(row_index, 0, "Accuracy", normal_format)
        worksheet.write(row_index, 1, f"{accuracy:.2f}", get_metric_format_to_use(accuracy))
        row_index += 1

        worksheet.write(row_index, 0, "Precision", normal_format)
        worksheet.write(row_index, 1, f"{precision:.2f}", get_metric_format_to_use(precision))
        row_index += 1

        worksheet.write(row_index, 0, "Recall", normal_format)
        worksheet.write(row_index, 1, f"{recall:.2f}", get_metric_format_to_use(recall))
        row_index += 1

        worksheet.write(row_index, 0, "F1 Score", normal_format)
        worksheet.write(row_index, 1, f"{f1:.2f}", get_metric_format_to_use(f1))
        row_index += 1

        # Add chat average score if available
        if chat_results:
            valid_scores = [
                float(cr.get("chat_score", "0"))
                for cr in chat_results
                if cr.get("chat_score") is not None
            ]
            if valid_scores:
                chat_avg_score = sum(valid_scores) / len(valid_scores)

                # Use existing score format variables (no color if score is 0)
                def get_score_format_to_use(value):
                    if value == "N/A" or value == 0:
                        return normal_format
                    if value >= 8.0:
                        return score_high_format
                    elif value >= 5.0:
                        return score_medium_format
                    else:
                        return score_low_format

                worksheet.write(row_index, 0, "Chat Avg Score", normal_format)
                worksheet.write(
                    row_index, 1, chat_avg_score, get_score_format_to_use(chat_avg_score)
                )

        # Store the end of metrics section
        metrics_end_row = row_index + 2

        # Configuration Info section (below metrics)
        config_start_row = metrics_end_row
        worksheet.write(config_start_row, 0, "Configuration Info", header_format)
        worksheet.merge_range(
            config_start_row, 0, config_start_row, 1, "Configuration Info", header_format
        )
        config_row = config_start_row + 1

        # Configuration details
        worksheet.write(config_row, 0, "Config Name", normal_format)
        worksheet.write(config_row, 1, config_name, normal_format)
        config_row += 1

        # Add actual config info if available
        if configs_info and config_name in configs_info:
            config_data = configs_info[config_name]
            vlm_config = config_data.get("VLM_Configurations", {})

            # Determine model name based on model type
            model_type = vlm_config.get("model", "Unknown")
            model_name = model_type

            if model_type == "openai-compat":
                model_name = vlm_config.get("VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME", "openai-compat")
            elif model_type == "custom" and vlm_config.get("model_path"):
                # Get the last part of the model path
                model_path = vlm_config.get("model_path", "")
                model_name = model_path.split("/")[-1] if "/" in model_path else model_path

            worksheet.write(config_row, 0, "Model Name", normal_format)
            worksheet.write(config_row, 1, model_name, normal_format)
            config_row += 1

            # VLM Parameters (read from vlmParams section in config)
            vlm_params = vlm_config.get("vlmParams", {})
            temperature = vlm_params.get("temperature", "N/A")
            top_p = vlm_params.get("top_p", "N/A")
            max_tokens = vlm_params.get("max_tokens", "N/A")
            top_k = vlm_params.get("top_k", "N/A")
            seed = vlm_params.get("seed", "N/A")

            worksheet.write(config_row, 0, "Temperature", normal_format)
            worksheet.write(config_row, 1, str(temperature), normal_format)
            config_row += 1

            worksheet.write(config_row, 0, "Top P", normal_format)
            worksheet.write(config_row, 1, str(top_p), normal_format)
            config_row += 1

            worksheet.write(config_row, 0, "Max Tokens", normal_format)
            worksheet.write(config_row, 1, str(max_tokens), normal_format)
            config_row += 1

            worksheet.write(config_row, 0, "Top K", normal_format)
            worksheet.write(config_row, 1, str(top_k), normal_format)
            config_row += 1

            worksheet.write(config_row, 0, "Seed", normal_format)
            worksheet.write(config_row, 1, str(seed), normal_format)
            config_row += 1
        else:
            worksheet.write(config_row, 0, "Model Name", normal_format)
            worksheet.write(config_row, 1, "See alert_config.yaml", normal_format)
            config_row += 1

        # Alert results section (underneath configuration info)
        alert_start_row = config_row + 3  # Start below config info with spacing
        alert_col = 0  # Start at column A

        worksheet.set_row(alert_start_row, 30)  # Set section header row height
        worksheet.write(alert_start_row, alert_col, "Alert Results", header_format)
        worksheet.merge_range(
            alert_start_row,
            alert_col,
            alert_start_row,
            alert_col + 4,
            "Alert Results",
            header_format,
        )
        alert_row = alert_start_row + 1

        # Alert results headers
        alert_headers = ["Test Case ID", "Prompt", "Ground Truth", "Generated Answer", "Match"]
        worksheet.set_row(alert_row, 30)  # Set header row height
        for col, header in enumerate(alert_headers):
            worksheet.write(alert_row, alert_col + col, header, header_format)
        alert_row += 1

        # Write detailed alert results with color coding and increased row height
        for result in results:
            is_match = result["generated_answer"] == result["ground_truth"]
            cell_format = match_format_compact if is_match else mismatch_format_compact

            # Set row height to triple normal (about 45 points)
            worksheet.set_row(alert_row, 45)

            worksheet.write(alert_row, alert_col, result["test_case_id"], cell_format)
            worksheet.write(alert_row, alert_col + 1, result["prompt"], cell_format)
            worksheet.write(alert_row, alert_col + 2, result["ground_truth"], cell_format)
            worksheet.write(alert_row, alert_col + 3, result["generated_answer"], cell_format)
            worksheet.write(alert_row, alert_col + 4, "✓" if is_match else "✗", cell_format)
            alert_row += 1

        # VLM Chat results section (underneath alert results)
        if chat_results:
            chat_start_row = alert_row + 2  # Start below alert results with spacing
            chat_col = 0  # Start at column A

            worksheet.set_row(chat_start_row, 30)  # Set section header row height
            worksheet.write(chat_start_row, chat_col, "VLM Chat Results", header_format)
            worksheet.merge_range(
                chat_start_row,
                chat_col,
                chat_start_row,
                chat_col + 4,
                "VLM Chat Results",
                header_format,
            )
            chat_row = chat_start_row + 1

            # Chat results headers (removed Reasoning column)
            chat_headers = [
                "Test Case ID",
                "Chat Prompt",
                "Expected Answer",
                "Generated Answer",
                "Score",
            ]
            worksheet.set_row(chat_row, 30)  # Set header row height
            for col, header in enumerate(chat_headers):
                worksheet.write(chat_row, chat_col + col, header, header_format)
            chat_row += 1

            # Write detailed chat results with color coding based on score and increased row height
            for chat_result in chat_results:
                # Determine color format based on score
                score = chat_result.get("chat_score", 0)
                try:
                    score_value = float(score) if score is not None else 0
                    if score_value >= 8:
                        cell_format = high_score_format
                    elif score_value >= 5:
                        cell_format = medium_score_format
                    else:
                        cell_format = low_score_format
                except (ValueError, TypeError):
                    cell_format = normal_format_compact

                # Set row height to triple normal (about 45 points)
                worksheet.set_row(chat_row, 45)

                worksheet.write(
                    chat_row, chat_col, chat_result.get("test_case_id", ""), cell_format
                )
                worksheet.write(
                    chat_row, chat_col + 1, chat_result.get("chat_prompt", ""), cell_format
                )
                worksheet.write(
                    chat_row, chat_col + 2, chat_result.get("chat_expected_answer", ""), cell_format
                )
                worksheet.write(
                    chat_row, chat_col + 3, str(chat_result.get("chat_result", "")), cell_format
                )
                worksheet.write(chat_row, chat_col + 4, score, cell_format)
                chat_row += 1

    # Create Health Eval sheet
    print("\nCollecting health eval data...")
    health_data = collect_health_eval_data("logs/accuracy/alert")
    
    if health_data:
        print(f"Found {len(health_data)} health eval records")
        create_health_eval_sheet(workbook, health_data, header_format, normal_format)
    else:
        print("No health eval data found")

    # Close workbook
    workbook.close()
    print(f"\nResults saved to: {output_file}")

    # Print summary to console as well
    for config_name, config_results in all_results.items():
        # Extract alert results from the new format
        results = config_results.get("alerts", [])
        chat_results = config_results.get("vlm_chat", [])

        if not results:
            continue

        print(f"\n{config_name}:")
        print(f"  Number of Alert Prompts: {len(results)}")
        print(f"  Number of Chat Prompts: {len(chat_results)}")

        # Extract metrics from configuration level (already calculated)
        confusion_matrix = config_results.get("accuracy_metrics", AccuracyMetrics())

        print(f"  Alert Accuracy: {confusion_matrix.accuracy():.2f}")
        print(f"  Alert Precision: {confusion_matrix.precision():.2f}")
        print(f"  Alert Recall: {confusion_matrix.recall():.2f}")
        print(f"  Alert F1 Score: {confusion_matrix.f1_score():.2f}")

        if chat_results:
            valid_scores = [
                float(cr.get("chat_score", 0))
                for cr in chat_results
                if cr.get("chat_score") is not None
            ]
            if valid_scores:
                avg_chat_score = sum(valid_scores) / len(valid_scores)
                print(f"  Average Chat Score: {avg_chat_score:.1f}")
            else:
                print("  Average Chat Score: No valid scores")
