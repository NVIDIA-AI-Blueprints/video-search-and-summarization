#!/usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# SPDX-License-Identifier: Apache-2.0


import http.server
import socketserver
import urllib.parse
import subprocess
import yaml
import os
import base64
import sys
import carla_camera_projection_demo as carla_utils

# --- Configuration Assets---
PORT = int(os.getenv("ACCURACY_PORT", "8000"))
CONFIG_PATH = "/tmp/config-file.yaml"
OUTPUT_IMG_1 = "/tmp/dt_frame.png"
OUTPUT_IMG_2 = "/tmp/real_world_frame.png"
#PYTHON_EXEC = "/app/.venv/bin/python3.10"
#TARGET_SCRIPT = "carla_camera_projection_demo.py"

# --- HTML Templates ---
HTML_FORM = """
<!DOCTYPE html>
<html>
<head>
    <title>Map Generator Config</title>
    <style>
        body { font-family: sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; }
        label { display: block; margin-top: 10px; font-weight: bold; }
        input[type="text"], input[type="number"], input[type="password"], textarea { width: 100%; padding: 8px; margin-top: 5px; box-sizing: border-box;}
        textarea { height: 150px; font-family: monospace; }
        input[type="submit"] { margin-top: 20px; padding: 10px 20px; font-size: 16px; background: #007bff; color: white; border: none; cursor: pointer; }
        input[type="submit"]:hover { background: #0056b3; }
        .group { border: 1px solid #ddd; padding: 15px; border-radius: 5px; margin-bottom: 20px; }
        h2 { border-bottom: 2px solid #333; padding-bottom: 5px; }

        /* --- Loading Overlay Styles --- */
        #loadingOverlay {
            display: none; /* Hidden by default */
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(255, 255, 255, 0.9);
            z-index: 1000;
            text-align: center;
            padding-top: 20%;
        }
        .spinner {
            border: 8px solid #f3f3f3;
            border-top: 8px solid #007bff;
            border-radius: 50%;
            width: 60px;
            height: 60px;
            animation: spin 1s linear infinite;
            margin: 0 auto 20px auto;
        }
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
    </style>
</head>
<body>

    <div id="loadingOverlay">
        <div class="spinner"></div>
        <h2>Generating Images...</h2>
        <p>This may take a few moments depending on the script execution time.</p>
    </div>

    <h1>Map Generator Interface</h1>
    <form id="configForm">
        
        <div class="group">
            <h2>Location & Camera</h2>
            <label>Center Lat: <input type="text" name="center_lat" value="22.624377"></label>
            <label>Center Lon: <input type="text" name="center_lon" value="120.286935"></label>
            <label>Carla Z: <input type="number" name="center_carla_z" value="120"></label>
            <label>Zoom: <input type="number" name="zoom" value="19"></label>
            <label>Size: <input type="text" name="size" value="1920x1080"></label>
            <label>FOV: <input type="number" name="fov" value="90"></label>
            <label>Yaw: <input type="number" name="yaw" value="-90"></label>
            <label>Map Type: <input type="text" name="maptype" value="satellite"></label>
        </div>

        <div class="group">
            <h2>API & Server</h2>
            <label>Google API Key: <input type="password" name="api_key" placeholder="Enter your API Key here..."></label>
            <label>Carla Map: <input type="text" name="carla_map" value="Kaohsiung"></label>
            <label>Host: <input type="text" name="host" value="carla-server"></label>
            <label>Port: <input type="number" name="port" value="2000"></label>
        </div>

        <div class="group">
            <h2>Coordinates</h2>
            <p>Format: JSON or YAML style list of lists. e.g. <code>[[lat, lon], [lat, lon]]</code></p>
            <textarea name="path_coordinates">
- [22.624738, 120.286149]
- [22.624364, 120.286882]
- [22.623423, 120.286410]
- [22.623373, 120.286535]
- [22.624403, 120.287070]
- [22.624830, 120.286210]
            </textarea>
        </div>

        <input type="submit" value="Generate Maps">
    </form>

    <script>
        // On page load, check if we're coming back from the result page
        window.addEventListener('DOMContentLoaded', function() {
            // Check if the "returnToForm" flag is set
            const shouldRestore = sessionStorage.getItem('returnToForm');
            
            if (shouldRestore === 'true') {
                // We're coming back from the result page, restore form data
                const savedData = sessionStorage.getItem('formData');
                if (savedData) {
                    try {
                        const formData = JSON.parse(savedData);
                        const form = document.getElementById('configForm');
                        
                        // Populate each form field with saved data
                        for (const [key, value] of Object.entries(formData)) {
                            const element = form.elements[key];
                            if (element) {
                                element.value = value;
                            }
                        }
                    } catch (e) {
                        console.error('Error restoring form data:', e);
                    }
                }
                
                // Clear the flag so a refresh will show defaults
                sessionStorage.removeItem('returnToForm');
            } else {
                // This is a fresh load or refresh, clear any old form data
                sessionStorage.removeItem('formData');
            }
        });

        document.getElementById('configForm').addEventListener('submit', function(e) {
            e.preventDefault(); // Stop the immediate standard submission
            
            // Save form data to sessionStorage before submitting
            const formData = new FormData(this);
            const formDataObj = {};
            for (const pair of formData) {
                formDataObj[pair[0]] = pair[1];
            }
            sessionStorage.setItem('formData', JSON.stringify(formDataObj));
            
            // Show the loading overlay
            document.getElementById('loadingOverlay').style.display = 'block';

            // Collect form data
            const searchParams = new URLSearchParams();
            for (const pair of formData) {
                searchParams.append(pair[0], pair[1]);
            }

            // Send data via Fetch API
            fetch('/', {
                method: 'POST',
                body: searchParams
            })
            .then(response => response.text())
            .then(html => {
                // Replace the entire document with the new HTML (the result page)
                document.open();
                document.write(html);
                document.close();
            })
            .catch(error => {
                console.error('Error:', error);
                document.getElementById('loadingOverlay').style.display = 'none';
                alert("An error occurred while connecting to the server.");
            });
        });
    </script>
</body>
</html>
"""

HTML_RESULT = """
<!DOCTYPE html>
<html>
<head>
    <title>Generation Results</title>
    <style>
        body {{ font-family: sans-serif; text-align: center; padding: 20px; }}
        .image-container {{ display: flex; flex-wrap: wrap; justify-content: center; gap: 20px; margin-top: 20px; }}
        .card {{ border: 1px solid #ccc; padding: 10px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); max-width: 45%; }}
        img {{ max-width: 100%; height: auto; display: block; margin-bottom: 10px; }}
        .btn {{ display: inline-block; padding: 10px 20px; background: #28a745; color: white; text-decoration: none; border-radius: 5px; }}
        .back-btn {{ display: inline-block; margin-top: 30px; padding: 10px 20px; background: #6c757d; color: white; text-decoration: none; border-radius: 5px; cursor: pointer; }}
    </style>
</head>
<body>
    <h1>Generation Successful</h1>
    <div class="image-container">
        <div class="card">
            <h3>Digital Twin (DT)</h3>
            <img src="data:image/png;base64,{img1_b64}" alt="DT Image">
            <a href="data:image/png;base64,{img1_b64}" download="DT.png" class="btn">Download DT.png</a>
        </div>
        <div class="card">
            <h3>Google Map</h3>
            <img src="data:image/png;base64,{img2_b64}" alt="Google Map">
            <a href="data:image/png;base64,{img2_b64}" download="Google-Map.png" class="btn">Download Google-Map.png</a>
        </div>
    </div>
    <br>
    <a href="/" class="back-btn" onclick="sessionStorage.setItem('returnToForm', 'true');">Go Back</a>
</body>
</html>
"""


class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # Serve the form on root
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(HTML_FORM.encode("utf-8"))
        else:
            # Fallback to default behavior (serving files) if needed
            super().do_GET()

    def do_POST(self):
        # gracefully and return them as HTML to the Javascript fetcher.
        try:
            # Parse Form Data
            content_length = int(self.headers["Content-Length"])
            post_data = self.rfile.read(content_length).decode("utf-8")
            form_data = urllib.parse.parse_qs(post_data)

            # Construct Configuration Dictionary
            def get_val(key, default=""):
                return form_data.get(key, [default])[0]

            # Parse the raw coordinates string back into a Python list
            raw_coords = get_val("path_coordinates")
            # Using yaml.safe_load to robustly handle the list text area
            coords_list = yaml.safe_load(raw_coords)

            GOOGLE_MAPS_API_KEY = (get_val("api_key") or os.getenv("GOOGLE_MAPS_API_KEY", ""))
            config_data = {
                "center_lat": float(get_val("center_lat", 0)),
                "center_lon": float(get_val("center_lon", 0)),
                "center_carla_z": int(get_val("center_carla_z", 0)),
                "zoom": int(get_val("zoom", 19)),
                "size": get_val("size", "1920x1080"),
                "fov": int(get_val("fov", 90)),
                "yaw": int(get_val("yaw", -90)),
                "maptype": get_val("maptype", "satellite"),
                "api_key": GOOGLE_MAPS_API_KEY,
                "path_coordinates": coords_list,
                "carla_map": get_val("carla_map", "Kaohsiung"),
                "host": get_val("host", "carla-server"),
                "port": int(get_val("port", 2000)),
            }

            # Write Config File
            with open(CONFIG_PATH, "w") as f:
                yaml.dump(config_data, f, default_flow_style=None)
            carla_utils.projection_processor(CONFIG_PATH)

            # Read Generated Images and Encode to Base64
            img1_b64 = self.image_to_base64(OUTPUT_IMG_1)
            img2_b64 = self.image_to_base64(OUTPUT_IMG_2)

            # Return Result Page
            response_html = HTML_RESULT.format(img1_b64=img1_b64, img2_b64=img2_b64)

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(response_html.encode("utf-8"))

        except Exception as e:
            error_msg = f"<h1>Processing Error</h1><pre>{str(e)}</pre>"
            self.send_response(500)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(error_msg.encode("utf-8"))

    def image_to_base64(self, filepath):
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Image not found at {filepath}")
        with open(filepath, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")


if __name__ == "__main__":
    # Use ThreadingTCPServer so the server doesn't freeze for others while generating
    with socketserver.ThreadingTCPServer(("", PORT), RequestHandler) as httpd:
        print(f"Serving at http://localhost:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
            httpd.shutdown()
