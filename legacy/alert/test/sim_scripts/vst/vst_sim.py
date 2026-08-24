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

from flask import Flask, jsonify, request, send_file
import uuid  # For generating unique stream IDs
import base64
import os
from datetime import datetime, timedelta, timezone

app = Flask(__name__)

# Pre-configured metadata for all names
def generate_response(names):
    response = []
    for name in names:
        # Generate a unique ID for each name
        unique_id = str(uuid.uuid4())  # Generate a new unique streamId for each entry

        # Build the JSON structure
        response.append({
            unique_id: [
                {
                    "isMain": True,
                    "metadata": {
                        "bitrate": "",
                        "codec": "H264",
                        "framerate": "30.000000",
                        "govlength": "",
                        "resolution": "1280x720"
                    },
                    "name": name,
                    "streamId": unique_id,
                    "url": f"rtsp://localhost:8554/live/{unique_id}",
                    "vodUrl": f"rtsp://localhost:8558/vod/{unique_id}"
                }
            ]
        })
    return response

# Define the HTTP endpoint
@app.route('/vst/api/v1/sensor/streams', methods=['POST', 'GET'])
def sensor_streams():
    # Example list of names provided by you
    names = [
        "HWY_20_AND_NW_ARTERIAL__PTZ",
        "HWY_20_AND_NW_ARTERIAL",
        "HWY_20_AND_LOCUST__WBA",
        "HWY_20_AND_LOCUST__EBA",
        "HWY_20_AND_OLD_HWY__NB",
        "HWY_20_AND_WACKER__EB",
        "HWY_20_AND_DEVON__EBA",
        "HWY_20_AND_DEVON__WBA",
        "HWY_20_AND_WACKER__WB",
        "HWY_20_AND_LOCUST__HILL_EB_26-76",
        "HWY_20_AND_JFK__PTZ",
        "HWY_20_AND_CENTURY__EB",
        "HWY_20_AND_UNIVERSITY__WBA",
        ":HWY_20_AND_LOCUST__HILL_EB_26-76",
        "HWY_20_AND_UNIVERSITY__NB", 
        "highway_cam_1"
    ]

    # Handle GET request (no Content-Type required)
    if request.method == 'GET':
        print("Received GET Request:", request.args)

    # Handle POST request (check for JSON body)
    if request.method == 'POST':
        print("Received POST Request:", request.json or {})

    # Generate the JSON response
    response_payload = generate_response(names)

    # Respond with the generated JSON
    return jsonify(response_payload)

# Add support for the endpoint that the service expects
@app.route('/vst/api/v1/live/streams', methods=['POST', 'GET'])
def live_streams():
    """Support the live/streams endpoint that the service expects."""
    return sensor_streams()  # Reuse the same logic as sensor/streams

# Add status endpoint for health checks
@app.route('/status', methods=['GET'])
def status():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "vst_simulator"})

@app.route('/vst/api/v1/record/<stream_id>/timelines', methods=['GET'])
def get_stream_timelines(stream_id):
    # Optional header check (do not block if missing)
    hdr = request.headers.get('streamId')
    if hdr is not None and hdr != stream_id:
        # Don't fail hard; return a 200 with an empty list to avoid confusion
        return jsonify([])

    # Return a window that always covers today (±1 year) so tests pass regardless of run date
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=365)).strftime('%Y-%m-%dT00:00:00.000Z')
    end = (now + timedelta(days=365)).strftime('%Y-%m-%dT23:59:59.999Z')
    timelines = [
        {
            "startTime": start,
            "endTime": end,
        }
    ]
    return jsonify(timelines)

# Update the picture endpoint to read and return a real image
@app.route('/vst/api/v1/live/stream/<stream_id>/picture', methods=['GET'])
def get_picture(stream_id):
    try:
        # Get the directory of the current script
        current_dir = os.path.dirname(os.path.abspath(__file__))
        image_path = os.path.join(current_dir, 'test_image.jpeg')
        
        # Read the image file and encode it as base64
        with open(image_path, 'rb') as image_file:
            image_data = image_file.read()
            base64_image = base64.b64encode(image_data).decode('utf-8')
        
        return jsonify({
            "status": "success",
            "data": base64_image
        })
    except Exception as e:
        print(f"Error reading image: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

# Add a live stream endpoint
@app.route('/vst/api/v1/live/stream/<stream_id>', methods=['GET'])
def get_stream(stream_id):
    return jsonify({
        "status": "success",
        "streamId": stream_id,
        "url": f"rtsp://localhost:8554/live/{stream_id}"
    })

# Storage module simulator: resolve media file path by VST id
@app.route('/api/v1/storage/file/path', methods=['GET'])
def storage_media_file_path():
    vst_id = request.args.get('id')
    if not vst_id:
        return jsonify({"error": "missing required query param: id"}), 400

    # For simulation, return a stable test filename; handler may prepend a base dir
    media_file = 'pick_up_box.mp4'

    return jsonify({
        'id': vst_id,
        'mediaFilePath': media_file
    })

# Storage module simulator: resolve temporary video URL for a time range
@app.route('/vst/api/v1/storage/file/url', methods=['GET'])
def storage_file_url():
    stream_id = request.args.get('streamId') or 'stream_001'
    start_time = request.args.get('startTime') or '2025-04-13T10:00:00Z'
    end_time = request.args.get('endTime') or '2025-04-13T10:05:00Z'
    expiry_minutes = int(request.args.get('expiryMinutes') or 2)
    container = request.args.get('container') or 'mp4'

    # Build a deterministic temp video URL that points back to this simulator
    file_name = f"{stream_id}.{container}"
    base = request.host_url.rstrip('/')
    video_url = f"{base}/vst/sim/media/{file_name}"

    return jsonify({
        'streamId': stream_id,
        'startTime': start_time,
        'endTime': end_time,
        'expiryMinutes': expiry_minutes,
        'container': container,
        'videoUrl': video_url
    })

@app.route('/vst/api/v1/storage/file/<stream_id>/url', methods=['GET'])
def get_storage_file_url(stream_id: str):
    """Return a dummy storage URL for the given stream id."""
    args = request.args
    base = request.host_url.rstrip('/')
    dummy_url = f"{base}/vst/sim/media/{stream_id}.mp4"
    url_payload = {
        "streamId": stream_id,
        "url": dummy_url,
        "videoUrl": dummy_url,
        "container": args.get("container", "mp4"),
        "startTime": args.get("startTime"),
        "endTime": args.get("endTime"),
        "expiryMinutes": int(args.get("expiryMinutes", 200)),
    }
    return jsonify(url_payload)

# Simple media-serving endpoint for simulator
@app.route('/vst/sim/media/<path:file_name>', methods=['GET', 'HEAD'])
def serve_sim_media(file_name: str):
    """Serve a test MP4 file or an in-memory fallback for URL validation in tests."""
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # Prefer an exact file if present, else try a known fallback, else serve in-memory bytes
        candidate = os.path.join(current_dir, file_name)
        fallback = os.path.join(current_dir, 'pick_up_box.mp4')

        if os.path.isfile(candidate):
            return send_file(candidate, mimetype='video/mp4', as_attachment=False)
        if os.path.isfile(fallback):
            return send_file(fallback, mimetype='video/mp4', as_attachment=False)

        # In-memory fallback: small non-empty payload to satisfy validation (200 + Content-Length > 0)
        from io import BytesIO
        dummy_bytes = b'\x00' * 2048
        dummy_stream = BytesIO(dummy_bytes)
        # Flask send_file can compute content-length from BytesIO in recent versions, but set header explicitly
        resp = send_file(dummy_stream, mimetype='video/mp4', as_attachment=False, download_name='dummy.mp4')
        resp.headers['Content-Length'] = str(len(dummy_bytes))
        return resp
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    # Run the Flask app on localhost, port 6000
    app.run(host='0.0.0.0', port=30888)
