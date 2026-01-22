#!/bin/bash
######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
######################################################################################################



# This script is used to override the remote endpoints for the VSS engine.
# It takes remote endpoints as arguments and generates the VSS helm chart override.yaml file.

# Function to display help message
show_help() {
    echo "Usage: export the necessary environment variables and run $(basename "$0")"
    echo 
    echo "Required environment variables:"
    echo "  CHART_NAME                Specify the chart name (default: nvidia-blueprint-vss-2.4.1.tgz)"
    echo "  HELM_URL                  Specify the helm repository URL (default: https://helm.ngc.nvidia.com/nvidia/blueprint/charts/)"
    echo "  NGC_API_KEY               Specify the NGC API key (mandatory)"
    echo "  LLM_BASE_URL              Specify the LLM base URL (optional)"
    echo "  LLM_MODEL                 Specify the LLM model (default: meta/llama-3.1-70b-instruct)"
    echo "  EMBEDDING_URL             Specify the embedding URL (optional)"
    echo "  RERANKER_URL              Specify the reranker URL (optional)"
    echo "  RIVA_ASR_SERVER_URI       Specify the Riva ASR server URL (optional)"
    echo "  RIVA_ASR_GRPC_PORT        Specify the Riva ASR gRPC port (optional)"
    echo "  RIVA_ASR_SERVER_USE_SSL   Specify if Riva ASR should use SSL (optional)"
    echo "  RIVA_ASR_SERVER_IS_NIM    Specify if Riva ASR server is NIM (optional)"
    echo "  RIVA_ASR_MODEL_NAME       Specify the Riva ASR model name (optional)"
    echo "  RIVA_ASR_SERVER_FUNC_ID   Function ID for the Riva ASR NIM from the Riva ASR NIM API page (optional)"
    echo
}

if [ $# -gt 0 ]; then
    show_help
    exit 1
fi

# Parse command line arguments
if [ -z "$NGC_API_KEY" ]; then
    echo "NGC_API_KEY is not set"
    exit 1
fi  
BASE_DIR=$(dirname "$(realpath "$0")")
# Set default value if not provided
if [ -z "$CHART_NAME" ]; then
    CHART_NAME="nvidia-blueprint-vss-2.4.1.tgz"
    echo "CHART_NAME is not provided, using default value: $CHART_NAME"
fi
if [ -z "$HELM_URL" ]; then
    HELM_URL="https://helm.ngc.nvidia.com/nvidia/blueprint/charts/"
    echo "HELM_URL is not provided, using default value: $HELM_URL"
fi
rm -rf $CHART_NAME
rm -rf nvidia-blueprint-vss

# Print the chart name (for verification)
echo "Fetching chart : $CHART_NAME"
if ! sudo microk8s helm fetch ${HELM_URL:-https://helm.ngc.nvidia.com/nvidia/blueprint/charts/}$CHART_NAME --username='$oauthtoken' --password=$NGC_API_KEY; then
    echo "Failed to fetch chart"
    echo "Please check HELM_URL ${HELM_URL} and CHART_NAME ${CHART_NAME}"
    exit 1
fi
#Untar the VSS package
tar -xvf $CHART_NAME >/dev/null

rm -rf $BASE_DIR/overrides.yaml
touch $BASE_DIR/overrides.yaml
cat << EOF >> $BASE_DIR/overrides.yaml
vss: 
  applicationSpecs: 
    vss-deployment: 
      initContainers: 
      - command: 
        - sh 
        - -c 
        - until nc -z -w 2 milvus-milvus-deployment-milvus-service 19530; do echo 
          waiting for milvus; sleep 2; done 
        image: busybox:1.37 
        imagePullPolicy: IfNotPresent 
        name: check-milvus-up 
      - command: 
        - sh 
        - -c 
        - until nc -z -w 2 neo-4-j-service 7687; do echo waiting for neo4j; sleep 
          2; done 
        image: busybox:1.37 
        imagePullPolicy: IfNotPresent 
        name: check-neo4j-up 

EOF
NUM_LINES=$(cat $BASE_DIR/overrides.yaml | wc -l)
LLM_CONFIGURATION_ENABLED=true
if [ -z "$LLM_BASE_URL" ]; then
    echo "LLM_BASE_URL is not provided, skipping LLM configuration"
    LLM_CONFIGURATION_ENABLED=false
fi

if [ -z "$LLM_MODEL" ] && [ "$LLM_CONFIGURATION_ENABLED" = true ]; then
    echo "LLM_MODEL is not provided, using meta/llama-3.1-70b-instruct as default"
    LLM_MODEL="meta/llama-3.1-70b-instruct"
fi


RIVA_ASR_CONFIGURATION_ENABLED=false
RIVA_ASR_REMOTE_SERVICE=false
RIVA_ASR_NVIDIA_NIM=false
if [ -n "$RIVA_ASR_SERVER_URI" ] && [ -n "$RIVA_ASR_GRPC_PORT" ] && [ -n "$RIVA_ASR_SERVER_USE_SSL" ]; then
    echo "RIVA_ASR configuration is provided, enabling RIVA ASR configuration"
    echo "RIVA_ASR_SERVER_URI: $RIVA_ASR_SERVER_URI"
    echo "RIVA_ASR_GRPC_PORT: $RIVA_ASR_GRPC_PORT"
    echo "RIVA_ASR_SERVER_USE_SSL: $RIVA_ASR_SERVER_USE_SSL"
    if [ -n "$RIVA_ASR_SERVER_FUNC_ID" ]; then
      echo "RIVA_ASR_SERVER_FUNC_ID: $RIVA_ASR_SERVER_FUNC_ID"
      echo "Using Riva ASR NIM from build.nvidia.com"
      RIVA_ASR_NVIDIA_NIM=true
    else
      if [ -n "$RIVA_ASR_SERVER_IS_NIM" ] && [ "$RIVA_ASR_SERVER_IS_NIM" = true ]; then
      # if ASR is a NIM model name is not needed
        echo "RIVA_ASR_SERVER_IS_NIM: $RIVA_ASR_SERVER_IS_NIM"
        echo "Using RIVA ASR as a remote service (NIM) RIVA ASR model name not needed"
        RIVA_ASR_REMOTE_SERVICE=true
      elif [ -n "$RIVA_ASR_SERVER_IS_NIM" ] && [ "$RIVA_ASR_SERVER_IS_NIM" = false ] && [ -n "$RIVA_ASR_MODEL_NAME" ]; then
        echo "RIVA_ASR_SERVER_IS_NIM: $RIVA_ASR_SERVER_IS_NIM"
        echo "RIVA_ASR_MODEL_NAME: $RIVA_ASR_MODEL_NAME"
        echo "Using RIVA ASR as a remote service"
        RIVA_ASR_REMOTE_SERVICE=true
      fi
    fi
    if [ "$RIVA_ASR_REMOTE_SERVICE" = true ] || [ "$RIVA_ASR_NVIDIA_NIM" = true ]; then
      RIVA_ASR_CONFIGURATION_ENABLED=true
    fi
fi
if [ "$RIVA_ASR_CONFIGURATION_ENABLED" = true ]; then
    #Remove the riva subchart: rm -r nvidia-blueprint-vss/charts/riva
    rm -rf $(find . -name riva -type d)
    #Update top Chart.yaml and remove dependency on riva sub chart
    sed -i '/^- condition: riva.enabled/,/^  version:/ s/^/#/' $BASE_DIR/nvidia-blueprint-vss/Chart.yaml

cat << EOF >> $BASE_DIR/overrides.yaml
      containers:
        vss:
          env:
          - name: VLM_MODEL_TO_USE
            value: cosmos-reason2
          - name: MODEL_PATH
            value: git:https://huggingface.co/nvidia/Cosmos-Reason2-8B
          - name: ENABLE_AUDIO
            value: "true"
          - name: RIVA_ASR_SERVER_URI
            value: ${RIVA_ASR_SERVER_URI}
          - name: RIVA_ASR_GRPC_PORT
            value: "${RIVA_ASR_GRPC_PORT}"
          - name: RIVA_ASR_SERVER_USE_SSL
            value: "${RIVA_ASR_SERVER_USE_SSL}"
EOF
  if [ "$RIVA_ASR_REMOTE_SERVICE" = true ]; then
cat << EOF >> $BASE_DIR/overrides.yaml
          - name: RIVA_ASR_SERVER_IS_NIM
            value: "${RIVA_ASR_SERVER_IS_NIM}"
EOF
    if [ "$RIVA_ASR_SERVER_IS_NIM" = false ]; then
cat << EOF >> $BASE_DIR/overrides.yaml
          - name: RIVA_ASR_MODEL_NAME
            value: ${RIVA_ASR_MODEL_NAME}
EOF
    fi
  fi
  if [ "$RIVA_ASR_NVIDIA_NIM" = true ]; then
cat << EOF >> $BASE_DIR/overrides.yaml
          - name: RIVA_ASR_SERVER_FUNC_ID
            value: "${RIVA_ASR_SERVER_FUNC_ID}"
          - name: RIVA_ASR_SERVER_API_KEY
            valueFrom:
             secretKeyRef:
               name: nvidia-api-key-secret
               key: NVIDIA_API_KEY
EOF
  fi
fi

if [ "$LLM_CONFIGURATION_ENABLED" = true ]; then
echo "Using LLM configuration : base_url: $LLM_BASE_URL, model: $LLM_MODEL"
    #Remove the nim-llm subchart: rm -r nvidia-blueprint-vss/charts/nim-llm/
    rm -rf $(find . -name nim-llm -type d)
    #Update top Chart.yaml and remove dependency on nim-llm sub chart
    sed -i '/^- name: nim-llm/,/^  version:/ s/^/#/' $BASE_DIR/nvidia-blueprint-vss/Chart.yaml
    #Comment out or remove the "check-llm-up" section.
    sed -i '/^      - args:/,/^        name: check-llm-up/ s/^/#/' $BASE_DIR/nvidia-blueprint-vss/values.yaml
fi

if [ -n "$EMBEDDING_URL" ]; then
    echo "Using EMBEDDING_URL: $EMBEDDING_URL"
    #Remove the nemo-embedding subcharts
    rm -rf $(find . -name nemo-embedding -type d)
    # Update top Chart.yaml and remove dependency on nemo-embedding
    sed -i '/^- name: nemo-embedding/,/^  version:/ s/^/#/' $BASE_DIR/nvidia-blueprint-vss/Chart.yaml
fi

if [ -n "$RERANKER_URL" ]; then
    echo "Using RERANKER_URL: $RERANKER_URL"
    #Remove the nemo-reranker subcharts
    rm -rf $(find . -name nemo-rerank -type d)
    # Update top Chart.yaml and remove dependency on nemo-reranker
    sed -i '/^- name: nemo-rerank/,/^  version:/ s/^/#/' $BASE_DIR/nvidia-blueprint-vss/Chart.yaml
fi

if [ "$LLM_CONFIGURATION_ENABLED" = true ] || [ -n "$EMBEDDING_URL" ] || [ -n "$RERANKER_URL" ]; then
cat << EOF >> $BASE_DIR/overrides.yaml
  configs: 
    ca_rag_config.yaml: 
      chat: 
EOF

if [ "$LLM_CONFIGURATION_ENABLED" = true ]; then
    cat << EOF >> $BASE_DIR/overrides.yaml
        llm: 
          base_url: ${LLM_BASE_URL}
          model: ${LLM_MODEL} 
EOF
fi

if [ -n "$EMBEDDING_URL" ]; then
cat << EOF >> $BASE_DIR/overrides.yaml
        embedding: 
          base_url: ${EMBEDDING_URL} 
EOF
fi

if [ -n "$RERANKER_URL" ]; then
cat << EOF >> $BASE_DIR/overrides.yaml
        reranker: 
          base_url: ${RERANKER_URL} 
EOF
fi

if [ "$LLM_CONFIGURATION_ENABLED" = true ]; then
cat << EOF >> $BASE_DIR/overrides.yaml
      notification: 
        llm: 
          base_url: ${LLM_BASE_URL} 
          model: ${LLM_MODEL} 
EOF
fi


if [ "$LLM_CONFIGURATION_ENABLED" = true ] || [ -n "$EMBEDDING_URL" ]; then
cat << EOF >> $BASE_DIR/overrides.yaml
      summarization: 
EOF
fi
if [ "$LLM_CONFIGURATION_ENABLED" = true ]; then
cat << EOF >> $BASE_DIR/overrides.yaml
        llm: 
          base_url: ${LLM_BASE_URL} 
          model: ${LLM_MODEL} 
EOF
fi
if [ -n "$EMBEDDING_URL" ]; then
cat << EOF >> $BASE_DIR/overrides.yaml
        embedding: 
          base_url: ${EMBEDDING_URL} 
EOF
fi


if [ "$LLM_CONFIGURATION_ENABLED" = true ] || [ -n "$EMBEDDING_URL" ]; then
cat << EOF >> $BASE_DIR/overrides.yaml
    guardrails_config.yaml: 
      models: 
EOF
fi
if [ "$LLM_CONFIGURATION_ENABLED" = true ]; then
cat << EOF >> $BASE_DIR/overrides.yaml
      - engine: nim 
        model: ${LLM_MODEL}
        parameters: 
          base_url: ${LLM_BASE_URL} 
        type: main 
EOF
fi
if [ -n "$EMBEDDING_URL" ]; then
cat << EOF >> $BASE_DIR/overrides.yaml
      - engine: nim
        model: nvidia/llama-3.2-nv-embedqa-1b-v2
        parameters: 
          base_url: ${EMBEDDING_URL} 
        type: embeddings
EOF
  if [ "$LLM_CONFIGURATION_ENABLED" = false ]; then
    grep "guardrails_config"  $BASE_DIR/nvidia-blueprint-vss/values.yaml -A 11 | grep "type: main" -B 4 >> overrides.yaml
    DEFAULT_LLM_URL=$(grep "guardrails_config"  $BASE_DIR/nvidia-blueprint-vss/values.yaml -A 11 | grep "type: main" -B 4 | grep base_url |  awk '{print $2}' )
    DEFAULT_LLM_MODEL=$(grep "guardrails_config"  $BASE_DIR/nvidia-blueprint-vss/values.yaml -A 11 | grep "type: main" -B 4 | grep model |  awk '{print $2}' )
    echo "Using the default LLM_BASE_URL $DEFAULT_LLM_URL and Model $DEFAULT_LLM_MODEL for Guardrails since none provided"
  fi
fi
fi

if [ $(cat $BASE_DIR/overrides.yaml | wc -l) -eq $(( $NUM_LINES + 1 )) ] ; then
    rm -rf $BASE_DIR/overrides.yaml
    echo "Not overriding any parameter for VSS helm chart"
    echo "Deleting overrides.yaml file"
    exit 0
fi
rm -rf $CHART_NAME
tar -czf $CHART_NAME nvidia-blueprint-vss

