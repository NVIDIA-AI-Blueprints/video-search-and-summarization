#!/bin/bash
set -e

# Usage function
usage() {
    echo "Usage: $0 workflow_output.json [output.csv] eval_output.json [...]"
    echo ""
    echo "Arguments:"
    echo "  workflow_output.json  Workflow output JSON file (required)"
    echo "  output.csv            Output CSV file (optional, default: report_output.csv)"
    echo "  eval_output.json ...  One or more evaluation output JSON files (required)"
    exit 1
}

# Require at least workflow file and one eval file
if [ "$#" -lt 2 ]; then
    usage
fi

WORKFLOW_FILE="$1"
shift

# Optional output file, default report_output.csv
if [ "$#" -ge 2 ]; then
    OUTPUT_FILE="$1"
    shift
else
    OUTPUT_FILE="report_output.csv"
fi

# Remaining args must be eval report files
if [ "$#" -lt 1 ]; then
    echo "Error: At least one eval_output.json file is required"
    usage
fi
EVAL_REPORT_FILES=("$@")

for report in "${EVAL_REPORT_FILES[@]}"; do
    if [ ! -f "$report" ]; then
        echo "Error: Eval report file '$report' not found"
        exit 1
    fi
done

if [ ! -f "$WORKFLOW_FILE" ]; then
    echo "Error: Workflow file '$WORKFLOW_FILE' not found"
    exit 1
fi

# Check if jq is available
if ! command -v jq &> /dev/null; then
    echo "Error: jq is required but not installed. Please install jq."
    exit 1
fi

# Remove existing output to ensure a clean run
rm -f "$OUTPUT_FILE"

# Convert JSON to CSV: id, query, score, error
# Cross-reference workflow.json to get query by id
# Skip reasoning column, error is empty if no error or populated with error message
jq -s -r --slurpfile workflow "$WORKFLOW_FILE" '
    # Create a lookup map from workflow: id -> query
    ($workflow[0] | if type == "array" then . else [.] end | map({(.id | tostring): .query}) | add) as $queries |
    
    ["id", "query", "score", "error"],
    (.[].eval_output_items[] |
        (.reasoning.sections?["Analysis Results"]?.section_score // null) as $sectionScore |
        (.score // null) as $itemScore |
        (if $sectionScore != null then (($sectionScore * 100 | round) / 100)
         elif $itemScore != null then $itemScore
         else "" end) as $scoreVal |
        (.reasoning | (if type == "object" then .error else null end) // "") as $errorVal |
        select(($scoreVal != "" and $scoreVal != null) or ($errorVal != "" and $errorVal != null)) |
        [
            .id,
            ($queries[.id | tostring] // ""),
            $scoreVal,
            $errorVal
        ]
    )
    | @csv
' "${EVAL_REPORT_FILES[@]}" > "$OUTPUT_FILE"

echo "CSV written to: $OUTPUT_FILE"
