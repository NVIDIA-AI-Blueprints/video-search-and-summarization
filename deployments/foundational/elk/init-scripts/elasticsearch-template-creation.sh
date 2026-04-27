#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

set -euo pipefail

# ELASTICSEARCH CONNECTION VARIABLES (parameterized from docker compose)
ELASTICSEARCH_CONNECTION_RETRY_ATTEMPTS="${ELASTICSEARCH_CONNECTION_RETRY_ATTEMPTS:-0}"
ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS="${ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS:-20}"
ELASTICSEARCH_URL="${ELASTICSEARCH_URL:-http://localhost:9200}"

BP_PROFILE=${BP_PROFILE:-}
echo "BP_PROFILE: ${BP_PROFILE}"

# Embedding dimensions for Elasticsearch dense_vector
ELASTICSEARCH_RTVI_CV_EMBEDDINGS_DIM=${ELASTICSEARCH_RTVI_CV_EMBEDDINGS_DIM:-1536}
ELASTICSEARCH_VISION_LLM_EMBEDDINGS_DIM=${ELASTICSEARCH_VISION_LLM_EMBEDDINGS_DIM:-768}

#################################
## function: check_ES_status
#################################
check_ES_status(){

    echo "Attempting to connect to the Elasticsearch server."

    # Wait for ES to come up
    until curl --output /dev/null --silent --head --fail -XGET "$ELASTICSEARCH_URL"; do
        if [ ${ELASTICSEARCH_CONNECTION_RETRY_ATTEMPTS} -eq ${ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS} ];then
            exit_with_msg "Max attempts to connect to ES reached."
        fi

        ELASTICSEARCH_CONNECTION_RETRY_ATTEMPTS=$(($ELASTICSEARCH_CONNECTION_RETRY_ATTEMPTS+1))
        echo "Unable to connect to ES. Trying to reconnect - (attempt $ELASTICSEARCH_CONNECTION_RETRY_ATTEMPTS/$ELASTICSEARCH_CONNECTION_MAX_ATTEMPTS)"
        sleep 5
    done
}

####################################
## function: create_index_template
####################################
create_index_template(){
    local template_name=$1
    local data_raw=$2

    echo "Creating index template: ${template_name}"

    response=$(curl -s -w "\\n%{http_code}" "${ELASTICSEARCH_URL}/_index_template/${template_name}" \
      -X 'PUT' \
      -H 'Content-Type: application/json' \
      --data-raw "$data_raw" \
      --compressed \
      --insecure)

    curl_exit_code=$?
    if [ $curl_exit_code -ne 0 ]; then
        exit_with_msg "Curl command failed with exit code ${curl_exit_code} for template '${template_name}'. Error: ${response}"
    fi

    http_code=$(echo "$response" | tail -n1)
    echo "HTTP code: ${http_code}"
    if [ "$http_code" != "200" ]; then
        response_body=$(echo "$response"| sed '$d')
        exit_with_msg "Failed to create index template '${template_name}'.\n  Status code: ${http_code}\n  Response: ${response_body}"
    fi
    echo "Successfully created index template: ${template_name}"
}

####################################
## function: setup_elasticsearch_templates
####################################
setup_elasticsearch_templates(){
    echo "Creating index templates."

    # metropolis_template - General settings for all mdx-* indices
    create_index_template "metropolis_template" '{
        "index_patterns": ["mdx-*"],
        "priority": 100,
        "template": {
          "settings": {
            "number_of_shards": 16,
            "translog.durability": "async",
            "refresh_interval": "2s"
          }
        }
      }'

    create_index_template "mdx_alerts_template" '{
        "index_patterns": ["mdx-alerts-*"],
        "priority": 501,
        "template": {
          "settings": {
            "index.lifecycle.name": "mdx-alerts-ilm-policy"
          },
          "mappings": {
            "properties": {
              "locations": { "type": "geo_shape" },
              "smoothLocations": { "type": "geo_shape" },
              "speedOverTime": { "enabled": false },
              "lipActivities": { "enabled": false },
              "gazes": { "enabled": false },
              "poses": { "enabled": false },
              "object": {
                "properties": {
                  "bbox": { "enabled": false },
                  "coordinate": { "enabled": false },
                  "dir": { "enabled": false },
                  "embedding": { "enabled": false },
                  "gaze": { "enabled": false },
                  "lipActivity": { "enabled": false },
                  "location": { "enabled": false },
                  "pose": { "enabled": false }
                }
              }
            }
          }
        }
      }'

    if [[ "${BP_PROFILE:-}" == "bp_developer_search" ]]; then
      create_index_template "mdx_behavior_template" '{
          "index_patterns": ["mdx-behavior-*"],
          "priority": 502,
          "template": {
            "settings": {
              "index.lifecycle.name": "mdx-behavior-ilm-policy",
              "index.mapping.exclude_source_vectors": false
            },
            "mappings": {
              "properties": {
                "locations": { "type": "geo_shape" },
                "smoothLocations": { "type": "geo_shape" },
                "speedOverTime": { "enabled": false },
                "lipActivities": { "enabled": false },
                "gazes": { "enabled": false },
                "poses": { "enabled": false },
                "embeddings": {
                  "type": "nested",
                  "properties": {
                    "vector": { "type": "dense_vector", "dims": '"${ELASTICSEARCH_RTVI_CV_EMBEDDINGS_DIM}"', "index": true }
                  }
                },

                "object": {
                  "properties": {
                    "bbox": { "enabled": false },
                    "coordinate": { "enabled": false },
                    "dir": { "enabled": false },
                    "embedding": { "enabled": false },
                    "gaze": { "enabled": false },
                    "lipActivity": { "enabled": false },
                    "location": { "enabled": false },
                    "pose": { "enabled": false }
                  }
                }
              }
            }
          }
        }'
      echo "Successfully created index template: mdx_behavior_template for bp_developer_search"
    else
      create_index_template "mdx_behavior_template" '{
        "index_patterns": ["mdx-behavior-*"],
        "priority": 502,
        "template": {
          "settings": {
            "index.lifecycle.name": "mdx-behavior-ilm-policy"
          },
          "mappings": {
            "properties": {
              "locations": { "type": "geo_shape" },
              "smoothLocations": { "type": "geo_shape" },
              "speedOverTime": { "enabled": false },
              "lipActivities": { "enabled": false },
              "gazes": { "enabled": false },
              "poses": { "enabled": false },
              "object": {
                "properties": {
                  "bbox": { "enabled": false },
                  "coordinate": { "enabled": false },
                  "dir": { "enabled": false },
                  "embedding": { "enabled": false },
                  "gaze": { "enabled": false },
                  "lipActivity": { "enabled": false },
                  "location": { "enabled": false },
                  "pose": { "enabled": false }
                }
              }
            }
          }
        }
      }'
    fi

    create_index_template "mdx_events_template" '{
        "index_patterns": ["mdx-events-*"],
        "priority": 503,
        "template": {
          "settings": {
            "index.lifecycle.name": "mdx-events-ilm-policy"
          },
          "mappings": {
            "properties": {
              "locations": { "type": "geo_shape" },
              "smoothLocations": { "type": "geo_shape" },
              "speedOverTime": { "enabled": false },
              "lipActivities": { "enabled": false },
              "gazes": { "enabled": false },
              "poses": { "enabled": false },
              "object": {
                "properties": {
                  "bbox": { "enabled": false },
                  "coordinate": { "enabled": false },
                  "dir": { "enabled": false },
                  "embedding": { "enabled": false },
                  "gaze": { "enabled": false },
                  "lipActivity": { "enabled": false },
                  "location": { "enabled": false },
                  "pose": { "enabled": false }
                }
              }
            }
          }
        }
      }'

    create_index_template "mdx_vlm_alerts_template" '{
        "index_patterns": ["mdx-vlm-alerts-*"],
        "priority": 504,
        "template": {
          "settings": {
            "index.lifecycle.name": "mdx-vlm-alerts-ilm-policy"
          },
          "mappings": {
            "properties": {
              "locations": { "type": "geo_shape" },
              "smoothLocations": { "type": "geo_shape" },
              "speedOverTime": { "enabled": false },
              "lipActivities": { "enabled": false },
              "gazes": { "enabled": false },
              "poses": { "enabled": false },
              "object": {
                "properties": {
                  "bbox": { "enabled": false },
                  "coordinate": { "enabled": false },
                  "dir": { "enabled": false },
                  "embedding": { "enabled": false },
                  "gaze": { "enabled": false },
                  "lipActivity": { "enabled": false },
                  "location": { "enabled": false },
                  "pose": { "enabled": false }
                }
              }
            }
          }
        }
      }'

    create_index_template "mdx_frames_template" '{
        "index_patterns": ["mdx-frames-*"],
        "priority": 505,
        "template": {
          "settings": {
            "index.lifecycle.name": "mdx-frames-ilm-policy"
          },
          "mappings": {
            "properties": {
              "objects": {
                "type": "nested",
                "properties": {
                  "bbox": { "enabled": false },
                  "bbox3d": { "enabled": false },
                  "coordinate": { "enabled": false },
                  "dir": { "enabled": false },
                  "embedding": { "enabled": false },
                  "gaze": { "enabled": false },
                  "lipActivity": { "enabled": false },
                  "location": { "enabled": false },
                  "pose": { "enabled": false }
                }
              },
              "rois": {
                "type": "nested",
                "properties": {
                  "coordinates": { "enabled": false }
                }
              },
              "fov": { "type": "nested" },
              "socialDistancing": {
                "properties": {
                  "clusters": { "enabled": false }
                }
              }
            }
          }
        }
      }'

    create_index_template "mdx_mtmc_template" '{
        "index_patterns": ["mdx-mtmc-*"],
        "priority": 506,
        "template": {
          "settings": {
            "index.lifecycle.name": "mdx-mtmc-ilm-policy"
          },
          "mappings": {
            "properties": {
              "matched": { "type": "nested" }
            }
          }
        }
      }'

    create_index_template "mdx_rtls_template" '{
        "index_patterns": ["mdx-rtls-*"],
        "priority": 507,
        "template": {
          "settings": {
            "index.lifecycle.name": "mdx-rtls-ilm-policy"
          },
          "mappings": {
            "properties": {
              "objects": {
                "type": "nested",
                "properties": {
                  "bbox": { "enabled": false },
                  "bbox3d": { "enabled": false },
                  "coordinate": { "enabled": false },
                  "dir": { "enabled": false },
                  "embedding": { "enabled": false },
                  "gaze": { "enabled": false },
                  "lipActivity": { "enabled": false },
                  "location": { "enabled": false },
                  "pose": { "enabled": false }
                }
              },
              "rois": {
                "type": "nested",
                "properties": {
                  "coordinates": { "enabled": false }
                }
              },
              "fov": { "type": "nested" },
              "socialDistancing": {
                "properties": {
                  "clusters": { "enabled": false }
                }
              }
            }
          }
        }
      }'

    create_index_template "mdx_amr_locations_template" '{
        "index_patterns": ["mdx-amr-locations-*"],
        "priority": 508,
        "template": {
          "settings": {
            "index.lifecycle.name": "mdx-amr-locations-ilm-policy"
          },
          "mappings": {
            "properties": {
              "objectCounts": { "type": "nested" },
              "locationsOfObjects": { "enabled": false }
            }
          }
        }
      }'

    create_index_template "mdx_amr_events_template" '{
        "index_patterns": ["mdx-amr-events-*"],
        "priority": 509,
        "template": {
          "settings": {
            "index.lifecycle.name": "mdx-amr-events-ilm-policy"
          },
          "mappings": {
            "properties": {
              "events": {
                "type": "nested",
                "properties": {
                  "blockages": { "enabled": false },
                  "currentRoute": { "enabled": false },
                  "newRoute": { "enabled": false },
                  "currentLocation": { "enabled": false }
                }
              }
            }
          }
        }
      }'

    create_index_template "mdx_bev_template" '{
        "index_patterns": ["mdx-bev-*"],
        "priority": 510,
        "template": {
          "settings": {
            "index.lifecycle.name": "mdx-bev-ilm-policy"
          },
          "mappings": {
            "properties": {
              "objects": {
                "type": "nested",
                "properties": {
                  "bbox": { "enabled": false },
                  "bbox3d": { "enabled": false },
                  "coordinate": { "enabled": false },
                  "dir": { "enabled": false },
                  "embedding": { "enabled": false },
                  "gaze": { "enabled": false },
                  "lipActivity": { "enabled": false },
                  "location": { "enabled": false },
                  "pose": { "enabled": false }
                }
              }
            }
          }
        }
      }'

    create_index_template "mdx_space_utilization_template" '{
        "index_patterns": ["mdx-space-utilization-*"],
        "priority": 511,
        "template": {
          "settings": {
            "index.lifecycle.name": "mdx-space-utilization-ilm-policy"
          },
          "mappings": {
            "properties": {
              "layouts": { "enabled": false }
            }
          }
        }
      }'

#   if rawDataSchema is in json format then comment the following template
    if [[ "${BP_PROFILE:-}" == "bp_developer_search" ]]; then
      create_index_template "mdx_raw_template" '{
          "index_patterns": ["mdx-raw-*"],
          "priority": 512,
          "template": {
            "settings": {
              "index.lifecycle.name": "mdx-raw-ilm-policy",
              "index.mapping.exclude_source_vectors": false
            },
            "mappings": {
              "properties": {
                "objects": {
                  "type": "nested",
                  "properties": {
                    "bbox": { "enabled": false },
                    "bbox3d": { "enabled": false },
                    "coordinate": { "enabled": false },
                    "dir": { "enabled": false },
                    "embedding": {
                      "properties": {
                        "vector": { "type": "dense_vector", "dims": '"${ELASTICSEARCH_RTVI_CV_EMBEDDINGS_DIM}"', "index": true }
                      }
                    },
                    "gaze": { "enabled": false },
                    "lipActivity": { "enabled": false },
                    "location": { "enabled": false },
                    "pose": { "enabled": false }
                  }
                }
              }
            }
          }
        }'
        echo "Successfully created index template: mdx_raw_template for bp_developer_search"
    else
      create_index_template "mdx_raw_template" '{
        "index_patterns": ["mdx-raw-*"],
        "priority": 512,
        "template": {
          "settings": {
            "index.lifecycle.name": "mdx-raw-ilm-policy"
          },
          "mappings": {
            "properties": {
              "objects": {
                "type": "nested",
                "properties": {
                  "bbox": { "enabled": false },
                  "bbox3d": { "enabled": false },
                  "coordinate": { "enabled": false },
                  "dir": { "enabled": false },
                  "embedding": { "enabled": false },
                  "gaze": { "enabled": false },
                  "lipActivity": { "enabled": false },
                  "location": { "enabled": false },
                  "pose": { "enabled": false }
                }
              }
            }
          }
        }
      }'
    fi

    create_index_template "mdx_incidents_template" '{
        "index_patterns": ["mdx-incidents-*"],
        "priority": 513,
        "template": {
          "settings": {
            "index.lifecycle.name": "mdx-incidents-ilm-policy"
          }
        }
      }'

    create_index_template "mdx_embed_filtered_template" '{
        "index_patterns": ["mdx-embed-filtered-*"],
        "priority": 514,
        "template": {
          "settings": {
            "index.lifecycle.name": "mdx-embed-filtered-ilm-policy",
            "index.mapping.exclude_source_vectors": false
          },
          "mappings": {
            "properties": {
              "llm": {
                "properties": {
                  "visionEmbeddings": {
                    "type": "nested",
                    "properties": {
                      "vector": { "type": "dense_vector", "dims": '"${ELASTICSEARCH_VISION_LLM_EMBEDDINGS_DIM}"', "index": true }
                    }
                  }
                }
              }
            }
          }
        }
      }'

    create_index_template "mdx_vlm_incidents_template" '{
        "index_patterns": ["mdx-vlm-incidents-*"],
        "priority": 515,
        "template": {
          "settings": {
            "index.lifecycle.name": "mdx-vlm-incidents-ilm-policy"
          }
        }
      }'

    echo "Successfully created index templates."
}

############################
## function: exit_with_msg
############################
exit_with_msg(){
    echo -e "$1 \nExiting Script."
    exit 1
}

######################
## Main
######################
main(){
    check_ES_status
    setup_elasticsearch_templates
}
main