# Endpoints

This document describes the configurable endpoints used by the Data Flywheel server and its components.

## Core Service Endpoints

### Data Storage
- **ELASTICSEARCH_URL**: URL for the Elasticsearch service (default: "http://df-elasticsearch:9200")
- **MONGODB_URL**: MongoDB service endpoint (default: "mongodb://df-mongodb:27017")
- **REDIS_URL**: Redis service endpoint (default: "redis://df-redis-master:6379/0")

## NeMo Platform Service Endpoints

### Entity Store
- **ENTITY_STORE_URL**: URL for the entity store service (default: "http://nemo-entity-store:8000")

### Data Store  
- **DATASTORE_URL**: URL for the data store service (default: "http://nemo-data-store:3000")

### Customizer
- **CUSTOMIZER_URL**: URL for the customizer service (default: "http://nemo-customizer:8000")

### Evaluator
- **EVALUATOR_URL**: URL for the evaluator service (default: "http://nemo-evaluator:7331")

### Guardrails
- **GUARDRAILS_URL**: URL for the guardrails service (default: "http://nemo-guardrails:7331")

### Deployment Management
- **DEPLOYMENT_MANAGEMENT_URL**: URL for deployment management service (default: "http://nemo-deployment-management:8000")

### NIM Proxy
- **NIM_PROXY_URL**: URL for the NIM proxy service (default: "http://nemo-nim-proxy:8000")

## LLM Judge Configuration

### Remote LLM Judge (Default)
- **LLM_JUDGE_URL**: URL for LLM judge service (default: "https://integrate.api.nvidia.com/v1/chat/completions")
- **LLM_JUDGE_MODEL**: Model name for LLM judge (default: "meta/llama-3.3-70b-instruct")

## Data Flywheel Configuration

### NMP Integration URLs
- **NEMO_BASE_URL**: Base URL for NeMo services (default: "http://nemo.test")
- **NIM_BASE_URL**: Base URL for NIM services (default: "http://nim.test")
- **DATASTORE_BASE_URL**: Base URL for data store (default: "http://data-store.test")

## Configuration Notes

1. Endpoints default to internal cluster service names.
4. Authentication is handled through secrets (NGC API key, NVIDIA API key, HF token).

