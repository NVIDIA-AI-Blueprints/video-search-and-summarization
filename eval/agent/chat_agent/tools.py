"""
Tools for the VSS Evaluation Agent.
"""

import glob
import json
import os
import subprocess
from datetime import datetime

from langchain_core.tools import tool

@tool
def validate_custom_model_path(model_path: str) -> bool:
    """Validate that a custom model path exists and is accessible."""
    return os.path.exists(model_path)

# Analysis Tools
@tool
def create_html_dashboard(
    logs_path: str, template_path: str = "agent/dashboard_template.html"
) -> str:
    """Create HTML dashboard with data filled in from logs."""
    try:
        # Load the HTML template
        with open(template_path, "r", encoding="utf-8") as f:
            template_content = f.read()

        # Collect all run data from logs
        dashboard_data = []

        # Find all log directories
        log_dirs = glob.glob(os.path.join(logs_path, "*"))
        log_dirs = [d for d in log_dirs if os.path.isdir(d)]

        for log_dir in log_dirs:
            run_data = {"health_data": None, "accuracy_data": None}

            # Find health summary file
            health_files = glob.glob(os.path.join(log_dir, "via_health_summary_*.json"))
            if health_files:
                try:
                    with open(health_files[0], "r") as f:
                        run_data["health_data"] = json.load(f)
                except Exception as e:
                    print(f"Warning: Could not parse health file {health_files[0]}: {e}")

            # Find accuracy log file
            accuracy_files = glob.glob(os.path.join(log_dir, "accuracy_*.log"))
            if accuracy_files:
                try:
                    with open(accuracy_files[0], "r") as f:
                        run_data["accuracy_data"] = json.loads(f.read().strip())
                except Exception as e:
                    print(f"Warning: Could not parse accuracy file {accuracy_files[0]}: {e}")

            # Only add runs that have at least some data
            if run_data["health_data"] or run_data["accuracy_data"]:
                dashboard_data.append(run_data)

        if not dashboard_data:
            return f"❌ No valid log data found in {logs_path}"

        # Convert data to JSON string for JavaScript
        data_json = json.dumps(dashboard_data, indent=2)

        # Replace the placeholder in template with actual data
        data_injection = f"""
        // Load actual dashboard data
        const dashboardData = {data_json};
        loadDashboardData(dashboardData);
        """

        # Replace the commented section with actual data loading
        if "// Load example data (remove in production)" in template_content:
            start_marker = "// Example data structure for testing (remove in production)"
            end_marker = "*/"

            start_idx = template_content.find(start_marker)
            if start_idx != -1:
                end_idx = template_content.find(end_marker, start_idx)
                if end_idx != -1:
                    template_content = (
                        template_content[:start_idx]
                        + data_injection
                        + template_content[end_idx + 2 :]
                    )
        else:
            # Fallback: inject before closing script tag
            template_content = template_content.replace(
                "</script>", f"{data_injection}\n    </script>"
            )

        # Generate output filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"vss_dashboard_{timestamp}.html"

        # Write the populated dashboard
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(template_content)

        print(f"✅ Dashboard created: {output_path}")
        print(f"📊 Processed {len(dashboard_data)} test runs")
        print(f"🌐 Open {output_path} in your browser to view the dashboard")

        return f"Dashboard created successfully: {output_path} with {len(dashboard_data)} runs"

    except FileNotFoundError:
        return f"❌ Template file not found: {template_path}"
    except Exception as e:
        return f"❌ Error creating dashboard: {str(e)}"
