# Configuration for Edge Node

This directory contains the primary configuration file for the Video Search & Summarization Edge Node.

## `config.yaml`

This is the single source of truth for all edge node services. It is structured into the following top-level sections:

| Section | Description |
| :--- | :--- |
| **device** | Global settings for the edge device, including unique IDs and local storage management policies. |
| **network** | Network-related settings for communication with the central server, including MQTT and API endpoints, and mTLS configuration. |
| **nvr_list** | A list of Network Video Recorders (NVRs) and the cameras to be monitored. This is the core multi-NVR support. |
| **ingest** | Configuration for the video ingestion service, such as chunk size and local clip limits. |
| **upload** | Settings for the upload worker, including API endpoints for the presigned upload flow and retry logic. |
| **sync** | Configuration for the synchronization worker, including polling intervals and endpoints for model and knowledge base updates. |

## `schema.json`

This file contains the JSON Schema used to validate `config.yaml`. The configuration loader (`edge_node/config_loader.py`) uses this schema to ensure the configuration is valid before any service starts.

## Validation

The configuration can be validated using the built-in CLI:

```bash
python -m edge_node.config_loader validate /path/to/config.yaml
```
