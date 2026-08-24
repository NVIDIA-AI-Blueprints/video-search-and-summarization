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

from flask import Flask, jsonify, request
import uuid
import time
import json
import os
from werkzeug.utils import secure_filename
from datetime import datetime, timezone

app = Flask(__name__)

# In-memory storage for uploaded files
uploaded_files = {}

# Mock model data
MODELS_RESPONSE = {
    "data": [
        {
            "id": "nvidia/neva-22b",
            "object": "model",
            "created": 1234567890,
            "owned_by": "nvidia"
        },
        {
            "id": "nvidia/vila-1.5-8b",
            "object": "model", 
            "created": 1234567890,
            "owned_by": "nvidia"
        }
    ]
}

# VSS Simulator - Always returns positive verification results

def generate_mock_response(prompt, media_type="image"):
    """Generate a mock positive response for all prompts"""
    # Always return a positive response that starts with "Yes" to ensure SUCCESS status
    return "Yes, the video analysis has been completed successfully and shows relevant content for the alert verification."

@app.route('/models', methods=['GET'])
def get_models():
    """Return available models"""
    print(f"[VSS SIM] GET /models - Returning {len(MODELS_RESPONSE['data'])} models")
    return jsonify(MODELS_RESPONSE)

@app.route('/files', methods=['POST'])
def upload_file():
    """Handle file upload for images and videos"""
    try:
        # Check if file is in request
        if 'file' not in request.files:
            return jsonify({"error": "No file provided"}), 400
        
        file = request.files['file']
        purpose = request.form.get('purpose', 'vision')
        media_type = request.form.get('media_type', 'image')
        
        if file.filename == '':
            return jsonify({"error": "No file selected"}), 400
        
        # Generate a unique media ID
        media_id = f"file-{uuid.uuid4().hex[:12]}"
        
        # Store file info (in real implementation, you'd save the file)
        uploaded_files[media_id] = {
            'filename': secure_filename(file.filename),
            'purpose': purpose,
            'media_type': media_type,
            'size': len(file.read()),
            'created_at': time.time()
        }
        
        print(f"[VSS SIM] POST /files - Uploaded {file.filename} as {media_id} (type: {media_type})")
        
        return jsonify({
            "id": media_id,
            "object": "file",
            "bytes": uploaded_files[media_id]['size'],
            "created_at": int(uploaded_files[media_id]['created_at']),
            "filename": uploaded_files[media_id]['filename'],
            "purpose": purpose
        })
        
    except Exception as e:
        print(f"[VSS SIM] Error uploading file: {e}")
        return jsonify({"error": "Upload failed"}), 500

@app.route('/v1/chat/completions', methods=['POST'])
@app.route('/chat/completions', methods=['POST'])
def chat_completions():
    """Handle chat completions"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        # Log headers for debugging
        print(f"\n[VSS SIM] === CHAT COMPLETIONS REQUEST ===")
        print(f"[VSS SIM] Headers: {dict(request.headers)}")
        
        # Log full payload for debugging entity-based parameters
        print(f"[VSS SIM] Full Payload:")
        print(json.dumps(data, indent=2))
        
        media_id = data.get('id')
        model = data.get('model')
        messages = data.get('messages', [])
        
        # Extract prompt from messages
        prompt = ""
        if messages and len(messages) > 0:
            prompt = messages[0].get('content', '')
        
        # Log key entity-based parameters
        print(f"[VSS SIM] Entity Parameters Detected:")
        print(f"  - temperature: {data.get('temperature', 'not set')}")
        print(f"  - max_tokens: {data.get('max_tokens', 'not set')}")
        print(f"  - top_p: {data.get('top_p', 'not set')}")
        print(f"  - top_k: {data.get('top_k', 'not set')}")
        print(f"  - seed: {data.get('seed', 'not set')}")
        print(f"  - chunk_duration: {data.get('chunk_duration', 'not set')}")
        print(f"  - highlight: {data.get('highlight', 'not set')}")
        
        print(f"[VSS SIM] POST /v1/chat/completions - Media ID: {media_id}, Model: {model}")
        print(f"[VSS SIM] Prompt: {prompt[:100]}...")
        
        # Simulate processing delay
        time.sleep(0.5)
        
        # Generate mock response based on prompt
        response_content = generate_mock_response(prompt)
        
        response = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_content
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": len(prompt.split()),
                "completion_tokens": len(response_content.split()),
                "total_tokens": len(prompt.split()) + len(response_content.split())
            }
        }
        
        print(f"[VSS SIM] Chat response: {response_content}")
        return jsonify(response)
        
    except Exception as e:
        print(f"[VSS SIM] Error in chat completions: {e}")
        return jsonify({"error": "Chat completion failed"}), 500

@app.route('/v1/summarize', methods=['POST'])
@app.route('/summarize', methods=['POST'])
def summarize():
    """Handle summarization requests"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        # Log headers for debugging
        print(f"\n[VSS SIM] === SUMMARIZE REQUEST ===")
        print(f"[VSS SIM] Headers: {dict(request.headers)}")
        
        # Log full payload for debugging entity-based parameters
        print(f"[VSS SIM] Full Payload:")
        print(json.dumps(data, indent=2))
        
        media_ids = data.get('id', [])
        model = data.get('model')
        prompt = data.get('prompt', '')
        
        # Log key entity-based parameters
        print(f"[VSS SIM] Entity Parameters Detected:")
        print(f"  - temperature: {data.get('temperature', 'not set')}")
        print(f"  - max_tokens: {data.get('max_tokens', 'not set')}")
        print(f"  - top_p: {data.get('top_p', 'not set')}")
        print(f"  - top_k: {data.get('top_k', 'not set')}")
        print(f"  - seed: {data.get('seed', 'not set')}")
        print(f"  - chunk_duration: {data.get('chunk_duration', 'not set')}")
        print(f"  - chunk_overlap_duration: {data.get('chunk_overlap_duration', 'not set')}")
        print(f"  - num_frames_per_chunk: {data.get('num_frames_per_chunk', 'not set')}")
        print(f"  - cv_metadata_overlay: {data.get('cv_metadata_overlay', 'not set')}")
        print(f"  - enable_caption: {data.get('enable_caption', 'not set')}")
        print(f"  - debug: {data.get('debug', 'not set')}")
        print(f"  - summarize_temperature: {data.get('summarize_temperature', 'not set')}")
        print(f"  - summarize_max_tokens: {data.get('summarize_max_tokens', 'not set')}")
        print(f"  - enable_chat: {data.get('enable_chat', 'not set')}")
        
        print(f"[VSS SIM] POST /v1/summarize - Media IDs: {media_ids}, Model: {model}")
        print(f"[VSS SIM] Prompt: {prompt[:100]}...")
        
        # Simulate processing delay
        time.sleep(1.0)
        
        # Generate mock response based on prompt
        response_content = generate_mock_response(prompt, "video" if any("video" in uploaded_files.get(mid, {}).get('media_type', '') for mid in media_ids) else "image")
        
        response = {
            "id": f"summarize-{uuid.uuid4().hex[:8]}",
            "object": "summarization",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_content
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": len(prompt.split()),
                "completion_tokens": len(response_content.split()),
                "total_tokens": len(prompt.split()) + len(response_content.split())
            }
        }
        
        print(f"[VSS SIM] Summarize response: {response_content}")
        return jsonify(response)
        
    except Exception as e:
        print(f"[VSS SIM] Error in summarization: {e}")
        return jsonify({"error": "Summarization failed"}), 500

@app.route('/reviewAlert', methods=['POST'])
def review_alert():
    """Simulated VSS verifyAlert endpoint.
    Accepts AlertRequestEntity-shaped JSON and returns an AlertResponseEntity-shaped JSON.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        # Log request for debugging
        print(f"\n[VSS SIM] === REVIEW ALERT REQUEST ===")
        print(f"[VSS SIM] Headers: {dict(request.headers)}")
        print(f"[VSS SIM] Full Payload:")
        print(json.dumps(data, indent=2))

        # Extract prompt from nested vssParams.vlmParams if present
        vss_params = data.get('vssParams', {}) or {}
        vlm_params = vss_params.get('vlmParams', {}) or {}
        prompt = vlm_params.get('prompt', '') or ''
        
        # Extract and log metadata
        meta_labels = data.get('metaLabels', [])
        print(f"[VSS SIM] Metadata Labels Found: {len(meta_labels)} items")
        if meta_labels:
            for meta in meta_labels:
                if isinstance(meta, dict) and 'key' in meta and 'value' in meta:
                    print(f"[VSS SIM]   - {meta['key']}: {meta['value']}")
                else:
                    print(f"[VSS SIM]   - Invalid metadata format: {meta}")

        # Derive mock reasoning and result from prompt
        response_content = generate_mock_response(prompt, "video")
        is_positive = response_content.strip().lower().startswith('yes')

        verification_status = 'SUCCESS' if is_positive else 'FAILURE'
        alert_status = 'VERIFIED' if is_positive else data.get('alert', {}).get('status', 'ACTIVE')

        # Compose response matching real VSS successful response style
        now_iso_z = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        alert_obj = data.get('alert', {}) or {}
        event_obj = data.get('event', {}) or {}

        response = {
            'id': data.get('id', f"{uuid.uuid4()}"),
            'version': data.get('version', '1.0'),
            '@timestamp': data.get('@timestamp', now_iso_z),
            'sensorId': data.get('sensorId', 'unknown'),
            'streamName': data.get('streamName'),
            'videoPath': data.get('videoPath', ''),
            'cvMetadataPath': data.get('cvMetadataPath', ''),
            'confidence': data.get('confidence', 0.95 if is_positive else 0.35),
            'alert': {
                'severity': alert_obj.get('severity', 'HIGH'),
                'status': alert_status,
                'type': alert_obj.get('type', 'UNKNOWN'),
                'description': alert_obj.get('description', 'Simulated alert verification')
            },
            'event': {
                'type': event_obj.get('type', 'verification'),
                'description': event_obj.get('description', 'Simulated verification event')
            },
            'verification': {
                'status': verification_status,
                'error_string': '' if is_positive else 'Simulated negative verification',
                'result': bool(is_positive),
                'verification_method': 'VSS',
                'verified_by': vlm_params.get('model', 'gpt-4o'),
                'verified_at': now_iso_z,
                'notes': 'Alert auto-verified by VSS; confidence above threshold.' if is_positive else 'Alert not verified by VSS; below threshold.',
                'debug': {
                    'input_prompt': prompt,
                    # seconds; consumer may convert to ms
                    'selected_frames_ts': [0.07, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5],
                    'metadata_processed': len(meta_labels),
                    'vss_params': vss_params,
                    'vlm_params': vlm_params
                },
                'description': '',
                'alert_reasoning': 'No reasoning available'
            },
            'metaLabels': meta_labels
        }

        print(f"[VSS SIM] reviewAlert response status: {verification_status}, result: {response['verification']['result']}")
        print(f"[VSS SIM] Metadata included in response: {len(meta_labels)} items")
        return jsonify(response)

    except Exception as e:
        print(f"[VSS SIM] Error in reviewAlert: {e}")
        return jsonify({"error": "reviewAlert failed"}), 500

@app.route('/files/<media_id>', methods=['DELETE'])
def delete_file(media_id):
    """Handle file deletion"""
    try:
        if media_id in uploaded_files:
            filename = uploaded_files[media_id]['filename']
            del uploaded_files[media_id]
            print(f"[VSS SIM] DELETE /files/{media_id} - Deleted {filename}")
            return jsonify({"deleted": True, "id": media_id})
        else:
            print(f"[VSS SIM] DELETE /files/{media_id} - File not found")
            return jsonify({"error": "File not found"}), 404
            
    except Exception as e:
        print(f"[VSS SIM] Error deleting file: {e}")
        return jsonify({"error": "Delete failed"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "VSS Simulator",
        "uploaded_files": len(uploaded_files),
        "timestamp": time.time()
    })

@app.route('/status', methods=['GET'])
def status():
    """Status endpoint showing current state"""
    return jsonify({
        "service": "VSS Simulator",
        "uploaded_files": len(uploaded_files),
        "files": list(uploaded_files.keys()),
        "endpoints": [
            "GET /models",
            "POST /files",
            "POST /v1/chat/completions", 
            "POST /v1/summarize",
            "POST /reviewAlert",
            "DELETE /files/{media_id}",
            "GET /health",
            "GET /status"
        ]
    })

if __name__ == '__main__':
    print("Starting VSS Simulator...")
    print("Available endpoints:")
    print("  GET /models - List available models")
    print("  POST /files - Upload images/videos")
    print("  POST /v1/chat/completions - Chat completions")
    print("  POST /v1/summarize - Summarization")
    print("  POST /reviewAlert - Alert verification")
    print("  DELETE /files/{media_id} - Delete uploaded files")
    print("  GET /health - Health check")
    print("  GET /status - Service status")
    print("\nRunning on http://0.0.0.0:8080")
    
    # Run the Flask app (debug=False to prevent fork/reloader issues)
    app.run(host='0.0.0.0', port=8080, debug=False) 