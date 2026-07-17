# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import glob
import os
import sys

import jellyfish
from langchain.schema import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

format = "Confluence wiki markup"
# format = "csv format for spreadsheet"


def load_data_from_folder(folder_path, file_prefixes):
    data = ""
    for prefix in file_prefixes:
        for file in glob.glob(os.path.join(folder_path, f"{prefix}*")):
            with open(file, "r") as f:
                data += f"{os.path.basename(file)}:\n{f.read()}\n\n"
    return data


# Minimal RAG using word similarity between query and data in files
def load_data_from_folder_narrow(folder_path, file_prefixes, query):
    """
    Load data from files in a folder, filtering lines that have at least one word
    with a similarity of 80% or more with the query.

    Args:
        folder_path (str): Path to the folder to search in.
        file_prefixes (list[str]): List of file prefixes to search for.
        query (str): Space-separated words to search for in the files (case insensitive).

    Returns:
        str: Concatenated text from files, with only lines having at least one word with
        a similarity of 50% or more with the query.
    """
    query_words = [word.lower().replace(",", "") for word in query.replace(",", " ").split()]
    data = ""
    for prefix in file_prefixes:
        for file in glob.glob(os.path.join(folder_path, f"{prefix}*")):
            with open(file, "r") as f:
                for line in f:
                    line_words = [
                        word.lower().replace(",", "") for word in line.replace(",", " ").split()
                    ]
                    for word in line_words:
                        for query_word in query_words:
                            similarity = jellyfish.jaro_winkler_similarity(word, query_word)
                            if similarity >= 0.8:
                                data += f"{os.path.basename(file)}: {line}"
                                break
    return data


def query_llm_with_data(llm, question, all_data):
    messages = [HumanMessage(content=question), AIMessage(content=all_data)]
    response = llm.invoke(messages)
    return response.content


def process_parent_folder(parent_folder_path, file_prefixes):
    # Get all immediate subdirectories
    subdirs = [
        d
        for d in os.listdir(parent_folder_path)
        if os.path.isdir(os.path.join(parent_folder_path, d))
    ]

    # Create the LLM instance
    llm = ChatOpenAI(
        model=os.getenv("LVS_LLM_MODEL_NAME", "meta/llama-3.1-70b-instruct"),
        base_url=os.getenv("LVS_LLM_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        api_key=os.getenv("NVIDIA_API_KEY"),
    )

    # Prepare the question
    question = f"Please create a table in {format} with the " "following columns:\n"
    question += ", ".join(subdirs) + "\n\n"
    question += "For each column, extract and fill in values for the " "following keys:\n"
    question += (
        "unique_test_code, gpu_names, num_gpus, vlm_model_name, "
        "vlm_batch_size, input_video_duration, chunk_size, num_chunks, "
        "total_vlm_input_tokens, total_vlm_output_tokens, "
    )
    question += (
        "decode_latency, vlm_pipeline_latency, ca_rag_latency, "
        "e2e_latency, pending_add_doc_latency, "
    )
    question += (
        "Use the data provided for each folder to fill in the corresponding " "column in the table."
    )
    question += "Please sort the data in alphabetic order of unique_test_code"

    # Collect data from all subdirectories
    all_data = {}
    for subdir in subdirs:
        folder_path = os.path.join(parent_folder_path, subdir)
        all_data[subdir] = load_data_from_folder_narrow(folder_path, file_prefixes, question)

    # Combine all data into a single string
    combined_data = "\n\n".join([f"{subdir}:\n{data}" for subdir, data in all_data.items()])

    print("combined_data", str(combined_data))

    # Query the LLM
    answer = query_llm_with_data(llm, question, combined_data)

    # Prepare the question

    question = (
        f"Please create a table in {format} with the "
        "following data having title columns and data in rows:\n"
    )
    # question += ", ".join(subdirs) + "\n\n"
    question += "unique_test_code, "
    question += "score_summary, score_vlm, score_chat\n\n"
    question += (
        "Use the data provided for each folder to fill in the corresponding "
        "column in the table.\n"
    )
    question += "Please sort the data in alphabetic order of unique_test_code"
    # question += (
    #     "Please also color code the scores: light red (35%) when score >=1 && <3"
    #     "light yellow (35%) when score >=3 && <7"
    #     "light green (35%) when score >=7 && <10"
    # )

    answer += "\n\n\n\n******\n\n\n\n"

    # Collect data from all subdirectories
    all_data = {}
    for subdir in subdirs:
        folder_path = os.path.join(parent_folder_path, subdir)
        all_data[subdir] = load_data_from_folder_narrow(folder_path, file_prefixes, question)

    # Combine all data into a single string
    combined_data = "\n\n".join([f"{subdir}:\n{data}" for subdir, data in all_data.items()])

    # Query the LLM
    answer += query_llm_with_data(llm, question, combined_data)

    with open("asklog_import_me_in_spreadsheet.csv", "w") as f:
        f.write(answer)

    return answer


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python script.py <parent_folder_path>")
        sys.exit(1)

    parent_folder_path = sys.argv[1]
    file_prefixes = ["accuracy_", "via_health_summary_"]  # add more prefixes as needed

    result = process_parent_folder(parent_folder_path, file_prefixes)
    print(result)
