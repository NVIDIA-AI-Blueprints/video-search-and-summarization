#!/usr/bin/env python3

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

import json
import os
import time

file_path = os.path.expanduser("~/sdg_aiva_tools.json")

records = []
with open(file_path) as file:
    for line in file:
        record = json.loads(line)
        records.append(record)

# Now `records` contains all the parsed JSONL records

print(len(records))

tool_count_mapping = {}

for record in records:
    tools = record.get("tools", [])
    count = len(tools)
    if count in tool_count_mapping:
        tool_count_mapping[count] += 1
    else:
        tool_count_mapping[count] = 1

print(tool_count_mapping)

tools_for_2 = set()
tools_for_4 = set()

for record in records:
    tools = record.get("tools", [])
    if len(tools) == 2:
        tools_for_2.add(json.dumps(tools))
    elif len(tools) == 4:
        tools_for_4.add(json.dumps(tools))

# Check if there is any common set of tools between records with 2 and 4 tools
common_tools = tools_for_2.intersection(tools_for_4)

if common_tools:
    print("Common tools found between records with 2 and 4 tools:", common_tools)
else:
    print("No common tools found between records with 2 and 4 tools.")


function_name_mapping = {}

for record in records:
    tools = record.get("tools", [])
    function_names = sorted(tool.get("function", {}).get("name", "wat") for tool in tools)
    function_names_str = ",".join(function_names)

    if function_names_str in function_name_mapping:
        function_name_mapping[function_names_str] += 1
    else:
        function_name_mapping[function_names_str] = 1

print(function_name_mapping)

# Assign unique workload_id to each function_names_str
function_name_to_workload_id = {}
for idx, fnames in enumerate(function_name_mapping.keys()):
    function_name_to_workload_id[fnames] = f"aiva_{idx + 1}"

# Build the new list of records in the requested format
final_records = []
for record in records:
    # 1. Pull out system prompt and first user message from messages
    messages = record.get("messages", [])
    system_prompt = None
    first_user_message = None
    for msg in messages:
        if msg.get("role") == "system" and system_prompt is None:
            system_prompt = msg
        elif msg.get("role") == "user" and first_user_message is None:
            first_user_message = msg
        if system_prompt and first_user_message:
            break
    # 2. Pull out tools
    tools = record.get("tools", [])
    # 3. Pull out first assistant response
    first_assistant_response = None
    for msg in messages:
        if msg.get("role") == "assistant":
            first_assistant_response = msg
            break
    # 4. Set workload_id based on function_name_mapping
    function_names = sorted(tool.get("function", {}).get("name", "wat") for tool in tools)
    function_names_str = ",".join(function_names)
    workload_id = function_name_to_workload_id.get(function_names_str, "unknown")
    # Build the new record
    new_record = {
        "system_prompt": system_prompt,
        "first_user_message": first_user_message,
        "tools": tools,
        "response": first_assistant_response,
        "workload_id": workload_id,
    }

    if first_assistant_response == "":
        print(json.dumps(record, indent=2))
        break

    final_records.append(new_record)

print(json.dumps(final_records[0], indent=2))

# Build the final dataset in the requested format
final_dataset = []
for rec in final_records:
    # request: OpenAIChatCompletionRequest
    request = {
        "model": "meta/llama-3.1-70b-instruct",
        "messages": [rec["system_prompt"], rec["first_user_message"]],
        "tools": rec["tools"],
    }
    # response: OpenAIChatCompletionResponse
    response = {"choices": [{"message": rec["response"]}]}
    # workload_id
    workload_id = rec["workload_id"]
    # client_id
    client_id = "dev"
    # timestamp
    timestamp = int(time.time())
    # Build the new record
    new_entry = {
        "request": request,
        "response": response,
        "workload_id": workload_id,
        "client_id": client_id,
        "timestamp": timestamp,
    }
    final_dataset.append(new_entry)

# Print the first entry as a sample
# print(json.dumps(final_dataset[0], indent=2))

# Save the final dataset to a JSON file
output_file = "final_dataset.jsonl"
with open(output_file, "w") as f:
    for entry in final_dataset:
        f.write(json.dumps(entry) + "\n")

print(f"Final dataset saved to {output_file}")
# Print or save the new list
# print(json.dumps(final_records, indent=2))
