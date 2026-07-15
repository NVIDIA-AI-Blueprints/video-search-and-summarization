<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Get a shell inside VIA container

make -C docker start INTERACTIVE=1

# Kill any instances of VIA server running in the container


# Setup
pip install pytest pytest-timeout
mkdir /tmp/via-logs

For tests that download streams from Artifactory (session setup), set credentials so downloads succeed:
export ARTIFACTORY_USER=your-username
export ARTIFACTORY_TOKEN=your-api-key

# Running Tests

# PERF Tests:

PYTHONPATH=src/:src/models/vila15/VILA pytest tests/test_via_server.py::test_perf -s | tee out.log

If you need to mask warnings not in pytest.ini file, add
#-p no:warnings --disable-warnings

# Collecting logs

Logs for PERF and Accuracy are written to: `logs/accuracy` folder.

Clear the folder before Test run:

rm -rf logs/accuracy

# Extract info using an LLM (Let it do the work)

pip install jellyfish

Now, to extract meaningful info as formatted tables, we can use the ask_on_files.py (ask an LLM and a simple similary-search based RAG to do this task).

Run command:
python3 tests/scripts/ask_on_files.py path/to/logs/accuracy

Output:
a) stdout
b) asklog_import_me_in_spreadsheet.csv

## Pull info into Spreadsheet - say google

File -> Import -> asklog_import_me_in_spreadsheet.csv

# Accuracy Tests

PYTHONPATH=src/:src/models/vila15/VILA pytest tests/test_via_server.py::test_qa_video_file_2_accuracy -s | tee out.log

## Details

Parameterized Test case to get PERF + Accuracy numbers:

test_via_server.py::test_qa_video_file_2_accuracy

Parameters configurable:

1)Media/Stream Input
2)Chunk size
3)VLM Model to use
4)PROMPTS X 4
5)GT for DC, Summary, Q&A

Other parameters that can be configured by changing start-up code in test_qa_video_file_2_accuracy:
1) --vlm-batch-size
2) --num-gpus
3) ALL configs we can supply via_server.py can be changed here.

The test runs a separate test case each for the "Configured Parameters" AND outputs a folder with results are logs automatically collected for each test-case:

## Output

Folder where results are collected for each TC:
logs/accuracy/

Now, to extract meaningful info as formatted tables, we can use the ask_on_files.py (ask an LLM to do this task).
