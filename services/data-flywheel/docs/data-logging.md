# Data Logging for AI Apps

Instrumenting your AI application to log interactions is a critical step in implementing the Data Flywheel. This guide explains how to enable data logging for any AI app, providing a general approach and best practices. A working example using an [AI Virtual Assistant (AIVA)](https://github.com/NVIDIA-AI-Blueprints/ai-virtual-assistant) is included for reference.

## General Approach and Requirements

### Supported Logging Backends

- **Elasticsearch** (default, recommended)
- (Extendable to other backends as needed)

### Environment Variables

To enable data logging, set the following environment variables:

```sh
ELASTICSEARCH_URL=http://your-elasticsearch-host:9200
ES_COLLECTION_NAME=flywheel  # Default index name
```

### Data Schema

Log entries should include:

```json
{
  "request": { ... },
  "response": { ... },
  "timestamp": "...",
  "client_id": "...",
  "workload_id": "..."
}
```

## Implementing Data Logging in Any App

### Direct Elasticsearch Integration (Recommended)

The Data Flywheel Blueprint uses direct Elasticsearch integration for logging. Here's a practical example:

```python
import os
import time
import uuid
from elasticsearch import Elasticsearch
from openai import OpenAI

# Environment configuration
ES_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
ES_INDEX = os.getenv("ES_COLLECTION_NAME", "flywheel")

# Initialize clients
es = Elasticsearch(hosts=[ES_URL])
openai_client = OpenAI()

CLIENT_ID = "my_demo_app"

# Example agent nodes (each with its own workload_id)
WORKLOADS = {
    "simple_chat": "agent.chat",
    "tool_router": "agent.tool_router",
}

def log_chat(workload_id: str, messages: list[dict]):
    # 1) call the LLM
    response = openai_client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=messages,
        temperature=0.3,
    )

    # 2) build the document
    doc = {
        "timestamp": int(time.time()),
        "workload_id": workload_id,
        "client_id": CLIENT_ID,
        "request": {
            "model": response.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 1024,
        },
        "response": response.model_dump(),  # OpenAI python-sdk v1 returns a pydantic model
    }

    # 3) write to Elasticsearch
    es.index(index=ES_INDEX, document=doc, id=str(uuid.uuid4()))

# --- Example usage -----------------------------------------------------------
messages_chat = [{"role": "user", "content": "Hello!"}]
log_chat(WORKLOADS["simple_chat"], messages_chat)

messages_tool = [
    {"role": "user", "content": "Who won the 2024 Super Bowl?"},
    {
        "role": "system",
        "content": "You are a router that decides whether to call the Wikipedia tool or answer directly.",
    },
]
log_chat(WORKLOADS["tool_router"], messages_tool)
```

### Integration Steps

1. Initialize the Elasticsearch client with the appropriate connection settings.
2. For each LLM interaction, capture both request and response data.
3. Structure the data according to the required schema with `workload_id` and `client_id`.
4. Index the log entry to Elasticsearch.

## Example: Instrumenting AIVA

This section provides a practical example of instrumenting an [AI Virtual Assistant (AIVA)](https://github.com/NVIDIA-AI-Blueprints/ai-virtual-assistant) application to log data for the Data Flywheel. It extends the general guidelines presented in the ["Instrumenting an application"](../README.md#2instrumenting-an-application) section of the main README.

### Configuration

To enable data logging to Elasticsearch for AIVA, configure the following environment variables:

```sh
ELASTICSEARCH_URL=http://your-elasticsearch-host:9200
ES_COLLECTION_NAME=flywheel  # Default index name
```

### Data Schema

The log entries stored in Elasticsearch contain the following structure:

```json
{
  "request": {
    "model": "model_name",
    "messages": [{"role": "user", "content": "..."}],
    "temperature": 0.2,
    "max_tokens": 1024,
    "tools": []
  },
  "response": {
    "id": "run_id",
    "object": "chat.completion",
    "model": "model_name",
    "usage": {"prompt_tokens": 50, "completion_tokens": 120, "total_tokens": 170}
  },
  "timestamp": 1715854074,
  "client_id": "aiva",
  "workload_id": "session_id"
}
```

### Implementation Architecture

The Data Flywheel system includes several components for data management:

1. **Elasticsearch Client**: Handles connections and indexing (`src/lib/integration/es_client.py`)
2. **Record Exporter**: Retrieves logged data for processing (`src/lib/integration/record_exporter.py`)
3. **Data Validation**: Ensures data quality before processing (`src/lib/integration/data_validator.py`)

### Code Implementation Examples

#### Elasticsearch Client Implementation

The system uses a robust Elasticsearch client:

```python
# From src/lib/integration/es_client.py (simplified for readability)
import os
import time
from elasticsearch import Elasticsearch, ConnectionError

ES_COLLECTION_NAME = os.getenv("ES_COLLECTION_NAME", "flywheel")
ES_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")

def get_es_client():
    """Get a working Elasticsearch client, retrying if needed."""
    for attempt in range(30):  # Try for up to 30 seconds
        try:
            client = Elasticsearch(hosts=[ES_URL])
            if client.ping():
                health = client.cluster.health()
                if health["status"] in ["yellow", "green"]:
                    # Create primary index if it doesn't exist
                    client.indices.refresh()
                    if not client.indices.exists(index=ES_COLLECTION_NAME):
                        client.indices.create(index=ES_COLLECTION_NAME, body=ES_INDEX_SETTINGS)
                    return client
            time.sleep(1)
        except ConnectionError as err:
            if attempt == 29:
                raise RuntimeError("Could not connect to Elasticsearch") from err
            time.sleep(1)
    
    raise RuntimeError("Elasticsearch did not become healthy in time")
```

#### Data Loading for Testing

For development and testing, you can load sample data using the unified data loader:

```bash
# Load LLM data
uv run python src/scripts/load_data.py --type llm \
  --workload-id your-workload \
  --client-id your-client \
  --file your_data.jsonl

# Load DPO data
uv run python src/scripts/load_data.py --type dpo \
  --workload-id your-workload \
  --client-id your-client \
  --file your_dpo_data.jsonl

# Load embedding data
uv run python src/scripts/load_data.py --type embedding \
  --workload-id your-workload \
  --client-id your-client \
  --file your_embedding_data.jsonl
```

Or programmatically in Python:

```python
# From src/scripts/load_data.py implementation
from src.lib.integration.es_client import get_es_client, ES_COLLECTION_NAME

def load_data_to_elasticsearch(
    workload_id: str = "",
    client_id: str = "",
    file_path: str = "aiva_primary_assistant_dataset.jsonl",
    index_name: str = ES_COLLECTION_NAME,
):
    """Load test data from JSON file into Elasticsearch."""
    es = get_es_client()
    
    with open(file_path) as f:
        test_data = [json.loads(line) for line in f]
    
    for doc in test_data:
        # Override identifiers if provided
        if workload_id:
            doc["workload_id"] = workload_id
        if client_id:
            doc["client_id"] = client_id
        
        es.index(index=index_name, document=doc)
    
    # Refresh the index to make data immediately available
    es.indices.refresh(index=index_name)
```

#### AIVA Data Transformation

For AIVA-specific data transformation, the system provides a transformation script:

```python
# From src/scripts/aiva.py - Transform AIVA conversation data to Data Flywheel format
import json
import time

# Function name mapping for workload identification
function_name_mapping = {}
for record in records:
    tools = record.get("tools", [])
    function_names = sorted(tool.get("function", {}).get("name", "wat") for tool in tools)
    function_names_str = ",".join(function_names)
    if function_names_str in function_name_mapping:
        function_name_mapping[function_names_str] += 1
    else:
        function_name_mapping[function_names_str] = 1

# Assign unique workload_id to each function_names_str
function_name_to_workload_id = {}
for idx, fnames in enumerate(function_name_mapping.keys()):
    function_name_to_workload_id[fnames] = f"aiva_{idx+1}"

# Transform each record to Data Flywheel format
final_dataset = []
for rec in final_records:
    # Build OpenAI-compatible request/response format
    request = {
        "model": "meta/llama-3.1-70b-instruct",
        "messages": [rec["system_prompt"], rec["first_user_message"]],
        "tools": rec["tools"],
    }
    
    response = {"choices": [{"message": rec["response"]}]}
    
    # Determine workload_id based on tool function names
    function_names = sorted(tool.get("function", {}).get("name", "wat") for tool in rec["tools"])
    function_names_str = ",".join(function_names)
    workload_id = function_name_to_workload_id.get(function_names_str, "unknown")
    
    new_entry = {
        "request": request,
        "response": response,
        "workload_id": workload_id,
        "client_id": "dev",
        "timestamp": int(time.time()),
    }
    final_dataset.append(new_entry)
```

### Dependencies

- `elasticsearch==8.17.2`

## Best Practices

- Use consistent `workload_id` values for accurate workload identification.
- Make sure you include error handling in logging routines.
- Be mindful of privacy and personally identifiable information (PII)—consider redacting or anonymizing as needed.
- Log only what's necessary for model improvement and debugging.
- Use the `ES_COLLECTION_NAME` environment variable to configure your index name.
- Ensure your Elasticsearch cluster is properly configured for the expected data volume.

## Data Validation

The system includes built-in data validation to ensure quality:

- **OpenAI Format Validation**: Ensures proper request/response structure
- **Workload Type Detection**: Automatically identifies tool-calling vs. generic conversations
- **Deduplication**: Removes duplicate entries based on user queries
- **Quality Filters**: Applies workload-specific quality checks

## Integration with Data Flywheel

Once data is logged to Elasticsearch, the Data Flywheel can:

1. **Export Records**: Use `RecordExporter` to retrieve data for processing
2. **Validate Data**: Apply quality filters and format validation
3. **Create Datasets**: Generate training and evaluation datasets
4. **Run Evaluations**: Compare model performance across different configurations

## Additional Resources

- [Instrumenting an application (README)](../README.md#2instrumenting-an-application)
- [Elasticsearch Python client](https://www.elastic.co/guide/en/elasticsearch/client/python-api/current/index.html)
- [Data Validation Guide](dataset-validation.md)
- Source code examples:
  - `src/lib/integration/es_client.py` - Elasticsearch integration
  - `src/lib/integration/record_exporter.py` - Data retrieval
  - `src/scripts/load_data.py` - Unified data loading utilities for all pipeline types
  - `src/scripts/aiva.py` - AIVA data transformation examples 