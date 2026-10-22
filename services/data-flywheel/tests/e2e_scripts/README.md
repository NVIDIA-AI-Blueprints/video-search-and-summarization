# End-to-End Integration Tests

This directory contains end-to-end integration tests for the Data Flywheel Blueprint.

## Setup

The e2e test script can run in two modes:

### Option 1: Standalone (Minimal Dependencies)
For running the script independently with minimal setup:

```bash
# Install only required dependencies
pip install requests elasticsearch

# Pull dataset files from Git LFS (if using default dataset)
git lfs pull

# Run standalone
python tests/e2e_scripts/simple_e2e_test.py
```

### Option 2: Full Environment (Recommended)
For complete development environment with all features:

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all dependencies
uv sync

# Pull dataset files from Git LFS
git lfs pull
```

## Quick Start

**Run E2E test with pytest:**
```bash
uv run pytest -m e2e_integration tests/e2e_scripts/
```

**Run as standalone script:**
```bash
# Minimal standalone (with dependency check)
python tests/e2e_scripts/simple_e2e_test.py

# With uv (recommended)
uv run python tests/e2e_scripts/simple_e2e_test.py

# With custom URLs
python tests/e2e_scripts/simple_e2e_test.py \
  --api-url https://my-api-server.com \
  --elasticsearch-url https://my-elasticsearch.com

# With custom dataset file (within project)
python tests/e2e_scripts/simple_e2e_test.py \
  --data-file data/my_custom_dataset.jsonl

# With external dataset file (outside project)
python tests/e2e_scripts/simple_e2e_test.py \
  --data-file /path/to/external/dataset.jsonl \
  --allow-external-files

# With all custom options
python tests/e2e_scripts/simple_e2e_test.py \
  --data-file /absolute/path/to/dataset.jsonl \
  --workload-id "custom-test" \
  --timeout 120 \
  --allow-external-files
```

### New Features

**🔒 Security & Path Validation:**
- By default, data files must be within the project directory (security)
- Use `--allow-external-files` flag to use datasets outside the project
- Automatic dependency checking with helpful error messages

**📦 Standalone Ready:**
- Works with minimal dependencies (`requests`, `elasticsearch`)
- Optional pytest support (script works without pytest installed)
- Automatic validation of required packages on startup

## Complete Documentation

For comprehensive documentation including:
- Prerequisites and setup instructions
- Custom URL configuration
- Pytest integration details
- Troubleshooting guide
- CI/CD integration examples
- Performance improvements and bulk indexing

**📖 See: [End-to-End Integration Testing Guide](../../docs/12-e2e-integration-testing.md)**

## Embedding Pipeline E2E Test

Test the embedding pipeline workflow including NIM deployment, base evaluation, customization, and customized evaluation.

**Quick Start:**
```bash
# With default embedding dataset
python tests/e2e_scripts/embedding_e2e_test.py

# With custom dataset
python tests/e2e_scripts/embedding_e2e_test.py \
  --data-file data/my_custom_embeddings.jsonl

# As pytest
pytest -m e2e_integration tests/e2e_scripts/embedding_e2e_test.py
```

### Data Format

The embedding test expects triplet format for query-document pairs:

**List format:**
```json
["query text", "positive doc", "negative doc"]
```

**Dict format:**
```json
{
  "query": "What is machine learning?",
  "pos_doc": "ML is a subset of AI...",
  "neg_doc": "Cooking recipes..."
}
```

### Key Differences from LLM Test

- **Data Format:** Triplet format (query, pos_doc, neg_doc) vs request/response
- **Collection:** `flywheel_embeddings_data` vs `flywheel`
- **Evaluations:** base-eval and customized-eval only (no ICL)
- **Metrics:** recall_5, recall_10, ndcg_cut_5, ndcg_cut_10
- **No LLM Judge:** Embeddings use retriever benchmarks (scidocs)

## Files

- `simple_e2e_test.py` - LLM pipeline E2E integration test (dual-mode: standalone/pytest)
- `embedding_e2e_test.py` - Embedding pipeline E2E integration test (dual-mode: standalone/pytest)
- `conftest.py` - Pytest configuration and custom command line options
- `__init__.py` - Package initialization
