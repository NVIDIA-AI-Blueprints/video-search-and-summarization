<!-- SPDX-License-Identifier: MIT -->
# Docker Readme
Uses standalone production build of the app.

Uses custom-server.js to start the server.

.env sample to use for docker run when running the Metropolis BP VSS UI app:
```
PORT=3001

RUN_APP_NAME=nv-metropolis-bp-vss-ui
NEXT_PUBLIC_APP_TITLE=VSS BLUEPRINT
NEXT_PUBLIC_APP_SUBTITLE=Warehouse

NEXT_PUBLIC_ENABLE_CHAT_TAB=true
NEXT_PUBLIC_WORKFLOW=Warehouse Management Agent
NEXT_PUBLIC_WEBSOCKET_CHAT_COMPLETION_URL=ws://127.0.0.1:8000/websocket
NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL=http://127.0.0.1:8000/chat/stream
NEXT_PUBLIC_WEB_SOCKET_DEFAULT_ON=false
NEXT_PUBLIC_CHAT_HISTORY_DEFAULT_ON=true
NEXT_PUBLIC_RIGHT_MENU_OPEN=false
NEXT_PUBLIC_ENABLE_INTERMEDIATE_STEPS=true
NEXT_PUBLIC_DARK_THEME_DEFAULT=true
NEXT_PUBLIC_SHOW_THEME_TOGGLE_BUTTON=true
NEXT_PUBLIC_SIDE_CHATBAR_COLLAPSED=true
NEXT_PUBLIC_AGENT_API_URL_BASE=http://127.0.0.1:8000/api/v1
NEXT_PUBLIC_CHAT_UPLOAD_FILE_ENABLE=true
NEXT_PUBLIC_CHAT_INPUT_MIC_ENABLED=false
NEXT_PUBLIC_INTERACTION_MODAL_CANCEL_ENABLED=false
NEXT_PUBLIC_CHAT_MESSAGE_EDIT_ENABLED=true
NEXT_PUBLIC_CHAT_MESSAGE_SPEAKER_ENABLED=true
NEXT_PUBLIC_CHAT_MESSAGE_COPY_ENABLED=true
NEXT_PUBLIC_CHAT_UPLOAD_FILE_METADATA_ENABLED=false
NEXT_PUBLIC_CHAT_UPLOAD_FILE_HIDDEN_MESSAGE_TEMPLATE=Can you show the video clip of the video {filenames} that I just uploaded?

# Upload file config template JSON - Configure form fields for file upload
# Format: {"fields": [<field1>, <field2>, ...]}
# Each field object:
#   - field-name: string - Name of the field (e.g., "embedding", "description")
#   - field-type: "boolean" | "string" | "number" | "select" - Input type
#   - field-default-value: any - Default value for the field
#   - field-options: string[] - Options for select type (e.g., ["Type 1", "Type 2"])
#   - changeable: boolean - Allow user to modify value (true=editable, false=readonly)
#   - tooltip-info: string - Tooltip text on hover
NEXT_PUBLIC_CHAT_UPLOAD_FILE_CONFIG_TEMPLATE_JSON='{
    "fields": [
        {
            "field-name": "embedding",
            "field-type": "boolean",
            "field-default-value": true,
            "changeable": false,
            "tooltip-info": ""
        }
    ]
}'

# Custom Agent Parameters JSON - Configure dynamic form fields for chat request
# Format: {"params": [<param1>, <param2>, ...]}
# Each param object:
#   - name: string - Parameter key sent to backend API (e.g., "llm_reasoning", "model")
#   - label: string - Display label shown in the form UI
#   - type: "boolean" | "string" | "number" | "select" - Input type
#   - default-value: any - Initial value for the parameter
#   - options: string[] - Options for select type (e.g., ["gpt-4", "gpt-3.5-turbo"])
#   - changeable: boolean - Allow user to modify value (true=editable, false=readonly)
#   - tooltip-info: string - Tooltip text on hover

NEXT_PUBLIC_CHAT_API_CUSTOM_AGENT_PARAMS_JSON='{
    "params": [
        {
            "name": "llm_reasoning",
            "label": "LLM Reasoning",
            "type": "boolean",
            "default-value": false,
            "changeable": true,
            "tooltip-info": ""
        },
        {
            "name": "vlm_reasoning",
            "label": "VLM Reasoning",
            "type": "boolean",
            "default-value": false,
            "changeable": true,
            "tooltip-info": ""
        }
    ]
}'

# Sidebar Nemo instance — prefix NEXT_PUBLIC_SIDEBAR_CHAT_* (each falls back to main NEXT_PUBLIC_* above when unset).
NEXT_PUBLIC_ENABLE_CHAT_SIDEBAR=false
NEXT_PUBLIC_CHAT_SIDEBAR_OPEN_DEFAULT=false
NEXT_PUBLIC_SIDEBAR_CHAT_WORKFLOW=Search Agent
NEXT_PUBLIC_SIDEBAR_CHAT_WEBSOCKET_CHAT_COMPLETION_URL=ws://127.0.0.1:8000/websocket
NEXT_PUBLIC_SIDEBAR_CHAT_HTTP_CHAT_COMPLETION_URL=http://127.0.0.1:8000/chat/stream
NEXT_PUBLIC_SIDEBAR_CHAT_WEB_SOCKET_DEFAULT_ON=false
NEXT_PUBLIC_SIDEBAR_CHAT_CHAT_HISTORY_DEFAULT_ON=true
NEXT_PUBLIC_SIDEBAR_CHAT_ENABLE_INTERMEDIATE_STEPS=true
NEXT_PUBLIC_SIDEBAR_CHAT_DARK_THEME_DEFAULT=true
NEXT_PUBLIC_SIDEBAR_CHAT_SIDE_CHATBAR_COLLAPSED=true
NEXT_PUBLIC_SIDEBAR_CHAT_AGENT_API_URL_BASE=http://127.0.0.1:8000/api/v1
NEXT_PUBLIC_SIDEBAR_CHAT_CHAT_UPLOAD_FILE_ENABLE=true
NEXT_PUBLIC_SIDEBAR_CHAT_CHAT_INPUT_MIC_ENABLED=false
NEXT_PUBLIC_SIDEBAR_CHAT_INTERACTION_MODAL_CANCEL_ENABLED=false
NEXT_PUBLIC_SIDEBAR_CHAT_CHAT_MESSAGE_EDIT_ENABLED=true
NEXT_PUBLIC_SIDEBAR_CHAT_CHAT_MESSAGE_SPEAKER_ENABLED=true
NEXT_PUBLIC_SIDEBAR_CHAT_CHAT_MESSAGE_COPY_ENABLED=true
NEXT_PUBLIC_SIDEBAR_CHAT_CHAT_UPLOAD_FILE_METADATA_ENABLED=false
NEXT_PUBLIC_SIDEBAR_CHAT_CHAT_UPLOAD_FILE_HIDDEN_MESSAGE_TEMPLATE=Can you show the video clip of the video {filenames} that I just uploaded?
NEXT_PUBLIC_SIDEBAR_CHAT_SHOW_THEME_TOGGLE_BUTTON=false
# Optional: NEXT_PUBLIC_SIDEBAR_CHAT_CHAT_UPLOAD_FILE_CONFIG_TEMPLATE_JSON=...
# Optional: NEXT_PUBLIC_SIDEBAR_CHAT_CHAT_API_CUSTOM_AGENT_PARAMS_JSON='...'

NEXT_PUBLIC_ENABLE_ALERTS_TAB=true
NEXT_PUBLIC_VST_API_URL=http://127.0.0.1:30888/vst/api
NEXT_PUBLIC_MDX_WEB_API_URL=http://127.0.0.1:8081
# Maximum number of incidents fetched per API request (applies to the initial load and each "Show more" page; allowed range 10–5000, default 100 when unset).
NEXT_PUBLIC_ALERTS_TAB_ALERTS_FETCH_MAX_RESULT_SIZE=100
NEXT_PUBLIC_ALERTS_TAB_ALERTS_FETCH_DEFAULT_TIME_WINDOW_IN_MINUTES=10
NEXT_PUBLIC_ALERTS_TAB_DEFAULT_AUTO_REFRESH_IN_MILLISECONDS=1000
NEXT_PUBLIC_ALERTS_TAB_VERIFIED_FLAG_DEFAULT=true
NEXT_PUBLIC_ALERTS_TAB_ALERT_REPORT_PROMPT_TEMPLATE=Generate a report for incident '{incidentId}' with sensor id {sensorId}.
# Max search time limit (0 = unlimited, or use: 10m, 2h, 3d, 1w, 2M, 1y)
NEXT_PUBLIC_ALERTS_TAB_MAX_SEARCH_TIME_LIMIT=0
NEXT_PUBLIC_ALERTS_TAB_MEDIA_WITH_OBJECTS_BBOX=true

NEXT_PUBLIC_ENABLE_SEARCH_TAB=true
NEXT_PUBLIC_SEARCH_TAB_MEDIA_WITH_OBJECTS_BBOX=true

NEXT_PUBLIC_ENABLE_DASHBOARD_TAB=true
NEXT_PUBLIC_DASHBOARD_TAB_KIBANA_BASE_URL=http://127.0.0.1:5601

NEXT_PUBLIC_ENABLE_MAP_TAB=true
NEXT_PUBLIC_MAP_URL=http://127.0.0.1:3002

NEXT_PUBLIC_ENABLE_VIDEO_MANAGEMENT_TAB=true
# Add RTSP button in Video Management tab (enabled by default, set to 'false' to hide)
NEXT_PUBLIC_VIDEO_MANAGEMENT_TAB_ADD_RTSP_ENABLE=true
# Upload Video button in Video Management tab (enabled by default, set to 'false' to hide)
NEXT_PUBLIC_VIDEO_MANAGEMENT_VIDEO_UPLOAD_ENABLE=true
```

.env sample to use for docker run when running the NeMo Agent Toolkit UI app:
```
PORT=3000
RUN_APP_NAME=nemo-agent-toolkit-ui
NEXT_PUBLIC_WORKFLOW=NeMo Agent Toolkit
NEXT_PUBLIC_WEBSOCKET_CHAT_COMPLETION_URL=ws://127.0.0.1:8000/websocket
NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL=http://127.0.0.1:8000/chat/stream
NEXT_PUBLIC_WEB_SOCKET_DEFAULT_ON=false
NEXT_PUBLIC_CHAT_HISTORY_DEFAULT_ON=false
NEXT_PUBLIC_RIGHT_MENU_OPEN=false
NEXT_PUBLIC_ENABLE_INTERMEDIATE_STEPS=true
NEXT_PUBLIC_DARK_THEME_DEFAULT=false
NEXT_PUBLIC_SIDE_CHATBAR_COLLAPSED=false
NEXT_PUBLIC_AGENT_API_URL_BASE=http://127.0.0.1:8000/api/v1
NEXT_PUBLIC_CHAT_UPLOAD_FILE_ENABLE=false
NEXT_PUBLIC_CHAT_INPUT_MIC_ENABLED=true
NEXT_PUBLIC_INTERACTION_MODAL_CANCEL_ENABLED=true
NEXT_PUBLIC_CHAT_MESSAGE_EDIT_ENABLED=true
NEXT_PUBLIC_CHAT_MESSAGE_SPEAKER_ENABLED=true
NEXT_PUBLIC_CHAT_MESSAGE_COPY_ENABLED=true
NEXT_PUBLIC_CHAT_UPLOAD_FILE_METADATA_ENABLED=false
NEXT_PUBLIC_CHAT_UPLOAD_FILE_HIDDEN_MESSAGE_TEMPLATE=Can you show the video clip of the video {filenames} that I just uploaded?

# Upload file config template JSON - Configure form fields for file upload
# Format: {"fields": [<field1>, <field2>, ...]}
# Each field object:
#   - field-name: string - Name of the field (e.g., "embedding", "description")
#   - field-type: "boolean" | "string" | "number" | "select" - Input type
#   - field-default-value: any - Default value for the field
#   - field-options: string[] - Options for select type (e.g., ["Type 1", "Type 2"])
#   - changeable: boolean - Allow user to modify value (true=editable, false=readonly)
#   - tooltip-info: string - Tooltip text on hover
NEXT_PUBLIC_CHAT_UPLOAD_FILE_CONFIG_TEMPLATE_JSON='{
    "fields": [
        {
            "field-name": "embedding",
            "field-type": "boolean",
            "field-default-value": true,
            "changeable": false,
            "tooltip-info": ""
        }
    ]
}'

# Custom Agent Parameters JSON - Configure dynamic form fields for chat request
# Format: {"params": [<param1>, <param2>, ...]}
# Each param object:
#   - name: string - Parameter key sent to backend API (e.g., "llm_reasoning", "model")
#   - label: string - Display label shown in the form UI
#   - type: "boolean" | "string" | "number" | "select" - Input type
#   - default-value: any - Initial value for the parameter
#   - options: string[] - Options for select type (e.g., ["gpt-4", "gpt-3.5-turbo"])
#   - changeable: boolean - Allow user to modify value (true=editable, false=readonly)
#   - tooltip-info: string - Tooltip text on hover

NEXT_PUBLIC_CHAT_API_CUSTOM_AGENT_PARAMS_JSON='{
    "params": [
        {
            "name": "llm_reasoning",
            "label": "LLM Reasoning",
            "type": "boolean",
            "default-value": false,
            "changeable": true,
            "tooltip-info": ""
        },
        {
            "name": "vlm_reasoning",
            "label": "VLM Reasoning",
            "type": "boolean",
            "default-value": false,
            "changeable": true,
            "tooltip-info": ""
        }
    ]
}'

```

**Note:** RUN_APP_NAME should match the name of the app in the apps folder. Default is 'nemo-agent-toolkit-ui'.

```bash
# Build the Docker image from the parent directory
docker build -t <image-name> -f Dockerfile .
# OR
# docker build -t <image-name> --build-arg BUILD_TYPE=prod -f Dockerfile .


# Run the container with environment variables from .env
# Ensure the .env file is present before running this command.
# Skip --env-file .env if no overrides are needed.
# For metropolis-spatial-ai deployment overrides refer to above .env sample section
docker run --env-file <path-to-env-file> -p 3000:3000 <image-name>
# OR pass environment variables as arguments
# docker run -e NEXT_PUBLIC_WORKFLOW="Agent" -p 3000:3000 <image-name>
```

## Debug inside the container

Create a debug container image:
```
docker build -t <image-name> --build-arg BUILD_TYPE=dev -f Dockerfile .
```

Since the resulting docker is a distroless docker image, if needed to run any commands to debug the container,
you can use the following command:
```
docker run --entrypoint=sh --rm -it --env-file <path-to-env-file> -p 3000:3000 <image-name>
```

To start the app inside the debug container:
```
node custom-server.js
```
