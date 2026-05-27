# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import os
import requests
import argparse

def upload_videos(source_directory: str, endpoint: str, count: int) -> None:
    """
    Upload video files from the source directory to the specified upload_url.
    Uploads the first 'count' videos it finds (supports both .mp4 and .mkv).

    :param source_directory: The directory to search for video files.
    :param endpoint: The full endpoint URL including IP and path.
    :param count: Number of videos to upload.
    """
    
    # Remove the protocol (http:// or https://) if it exists
    if endpoint.startswith("http://"):
        endpoint = endpoint[len("http://"):]
    elif endpoint.startswith("https://"):
        endpoint = endpoint[len("https://"):]
    
    upload_url = f"http://{endpoint}/api/v1/storage/file"
    uploaded_count = 0

    # Loop through all files in the directory and upload the .mp4 and .mkv files
    for filename in os.listdir(source_directory):
        if uploaded_count >= count:
            break

        if filename.endswith(('.mp4', '.mkv')):
            source_file_path = os.path.join(source_directory, filename)
            with open(source_file_path, 'rb') as file:
                headers = {
                    'nvstreamer-chunk-number': '1',
                    'nvstreamer-total-chunks': '1',
                    'nvstreamer-is-last-chunk': 'true',
                    'nvstreamer-identifier': "identifier",
                    'nvstreamer-file-name': filename
                }
                files = {'file': (filename, file)}
                response = requests.post(upload_url, files=files, headers=headers)

                if response.status_code == 200:
                    print(f"Successfully uploaded {filename}")
                    uploaded_count += 1
                else:
                    print(f"Failed to upload {filename}: {response.status_code} - {response.text}")

    if uploaded_count < count:
        print(f"Only {uploaded_count} videos were uploaded. Unable to reach the specified count of {count}.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Upload video files (mp4 or mkv) from a specified directory to an endpoint API."
    )
    parser.add_argument(
        'endpoint',
        type=str,
        help='The full endpoint URL (IP:Port/Path) of the upload API endpoint, e.g., 10.0.0.1:30081/nvstreamer-2/'
    )
    parser.add_argument(
        'count',
        type=int,
        help='The number of video files to upload from the source directory.'
    )
    parser.add_argument(
        'source_directory',
        type=str,
        nargs='?',  # Optional argument
        default='.',
        help='The directory containing video files. Defaults to the current directory if not specified.'
    )

    args = parser.parse_args()

    upload_videos(args.source_directory, args.endpoint, args.count)

