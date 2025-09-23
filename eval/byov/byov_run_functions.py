######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

import csv
import glob
import json
import os
import shutil
import time


def dump_dict_to_json(data_dict, output_file):
    """
    Dump a Python dictionary to a JSON file.

    Args:
        data_dict (dict): The dictionary to dump
        output_file (str): Path to the output JSON file
    """
    with open(output_file, "w") as f:
        json.dump(data_dict, f, indent=4)


def collect_responses(
    groundtruthfile,
    chat_func,
    server,
    video_id,
    model,
    chunk_size,
    temperature,
    top_p,
    top_k,
    max_new_tokens,
    seed,
):
    """
    Function to collect responses to questions from a chat function.
    """

    with open(groundtruthfile) as f:
        ground_truth = json.load(f)

    total_time = 0
    num_calls = 0
    responses = []

    for i, qa_pair in enumerate(ground_truth):
        question = qa_pair["prompt"]
        answer = qa_pair["completion"]
        print(f"QUESTION {i+1}/{len(ground_truth)}: {question}")
        start_time = time.time()
        try:
            response = chat_func(
                question,
                server,
                video_id,
                model,
                chunk_size,
                temperature,
                top_p,
                top_k,
                max_new_tokens,
                seed,
            )
        except Exception as e:
            print(f"Error calling chat function: {e}")
            response = f"Error calling chat function: {e}"
            continue
        print(f"RESPONSE {i+1}/{len(ground_truth)}: {response}")
        print(f"GROUND TRUTH {i+1}/{len(ground_truth)}: {answer}")
        responses.append(response)
        end_time = time.time()

        call_time = end_time - start_time
        total_time += call_time
        num_calls += 1

        print()

    print(
        f"Average time taken for {num_calls} calls to {chat_func.__name__}: "
        f"{total_time / num_calls:.4f} seconds"
    )

    return responses


def evaluate_qa(responses, evaluator, groundtruthfile):
    """
    Function to evaluate the responses from q&a.
    """
    scores = []
    reasonings = []
    num_calls = 0
    avg_chat_score = 0
    with open(groundtruthfile) as file:
        ground_truth = json.load(file)

        for i, qa_pair in enumerate(ground_truth):
            question = qa_pair["prompt"]
            answer = qa_pair["completion"]
            response = responses[num_calls]
            attempts = 0

            # loop to handle the error when evaluator returns a value error
            while attempts < 3:
                try:
                    eval_result = evaluator.evaluate_strings(
                        prediction=response,
                        reference=answer,
                        input=question,
                    )
                except ValueError as e:
                    print(f"Error evaluating qa: {e}")
                    print(f"trying again {attempts+1}/3")
                    attempts += 1
                    continue
                break
            if attempts == 3:
                print("Error evaluating qa: max attempts reached")
                return "evaluate_qa error", "evaluate_qa error", "evaluate_qa error"

            scores.append(float(eval_result["score"]))
            avg_chat_score += float(eval_result["score"])
            reasonings.append(eval_result["reasoning"])
            num_calls += 1
            print(f"Score for Q{i+1}:", eval_result["score"])
            print(f"Reasoning for Q{i+1}:", eval_result["reasoning"])
            print()

        avg_chat_score = avg_chat_score / len(scores) if len(scores) > 0 else 0

        # write the scores and reasonings to a csv file
        with open(f"{groundtruthfile}.result.csv", "w") as f:
            writer = csv.writer(f)
            writer.writerow(["Question", "Answer", "Response", "Score"])
            for i in range(len(ground_truth)):
                writer.writerow(
                    [
                        ground_truth[i]["prompt"],
                        ground_truth[i]["completion"],
                        responses[i],
                        scores[i],
                    ]
                )

    return scores, reasonings, num_calls, avg_chat_score


def evaluate_summary(summary_response, evaluator, groundtruthfile, prompt):
    """
    Function to evaluate the summary.
    """

    try:
        with open(groundtruthfile) as file:
            ground_truth = json.load(file)

        answer = ground_truth["completion"]

        attempts = 0
        while attempts < 3:
            try:
                eval_result = evaluator.evaluate_strings(
                    prediction=summary_response,
                    reference=answer,
                    input=prompt,
                )
            except ValueError as e:
                print(f"Error evaluating summary: {e}")
                print(f"trying again {attempts+1}/3")
                attempts += 1
                continue
            break

        if attempts == 3:
            print("Error evaluating summary: max attempts reached")
            return "evaluate_summary error", "evaluate_summary error"
        print("Summary Score: ", eval_result["score"])
        print("Summary Reasoning: ", eval_result["reasoning"])
        print("--------------------------------")

    except Exception as e:
        print("Error evaluating summary: ", e)
        return "evaluate_summary error", "evaluate_summary error"

    return eval_result["score"], eval_result["reasoning"]


def get_ground_truth_for_chunk(chunk_id, ground_truth_dc):
    """
    Function to get the ground truth for a chunk.
    """
    return ground_truth_dc.get(str(chunk_id), None)


def evaluate_dc(vlm_dc_out_file, evaluator, groundtruthfile, prompt):
    """
    Function to evaluate the dense caption.
    """
    dc_eval_result = {}

    try:
        with open(groundtruthfile, "r") as file:
            ground_truth_dc = json.load(file)

    except Exception as e:
        print("Error evaluating dense caption ground truth: ", e)
        return "evaluate_dc error", {}

    testdata = {}
    with open(vlm_dc_out_file, "r") as testdata_csv:
        reader = csv.DictReader(testdata_csv)
        for row in reader:
            chunk_id = row["Chunk_ID"]
            testdata[chunk_id] = row["Answer"]

    for chunk_id in testdata:
        expected = get_ground_truth_for_chunk(chunk_id, ground_truth_dc)
        if expected is None:
            print(
                f"Warning: Chunk_ID '{chunk_id}' not found in dense caption ground truth, skipping"
            )
            continue
        if chunk_id in testdata:
            prediction = testdata[chunk_id]
        else:
            print(f"Warning: Chunk_ID '{chunk_id}' not found in dense caption response, skipping")
            continue
        attempts = 0
        while attempts < 3:
            try:
                eval_result = evaluator.evaluate_strings(
                    prediction=prediction,
                    reference=expected,
                    input=prompt,
                )
            except ValueError as e:
                print(f"Error evaluating dense caption: {e}")
                print(f"trying again {attempts+1}/3")
                attempts += 1
                continue
            break
        if attempts == 3:
            print("Error evaluating dense caption: max attempts reached")
            return "evaluate_dc error", {}

        print(f"Chunk {chunk_id} Dense Caption Score: {eval_result['score']}")
        print(f"Chunk {chunk_id} Dense Caption Reasoning: {eval_result['reasoning']}")
        print()

        dc_eval_result[chunk_id] = {}
        dc_eval_result[chunk_id]["score"] = float(eval_result["score"])
        dc_eval_result[chunk_id]["reasoning"] = eval_result["reasoning"]

    avg_dc_score = (
        sum(dc_eval_result[chunk_id]["score"] for chunk_id in dc_eval_result) / len(dc_eval_result)
        if dc_eval_result
        else 0
    )

    return avg_dc_score, dc_eval_result


def byov_create_dir_and_move_files(
    req_id,
    MEDIA_PATH,
    CHUNK_SIZE,
    GROUND_TRUTH_FILE="",
    DC_GT=None,
    SUMMARY_GT=None,
    PROMPT_1=None,
    PROMPT_3=None,
    evaluator=None,
    test_data=None,
    video_id=None,
    VLM=None,
    summary_score=None,
    avg_chat_score=None,
):
    """
    This function takes in an argument `req_id` and performs the following activities:
    1. Creates a directory with the name
    `logs/accuracy/$req_id/$VLM_MODEL_TO_USE/$video-file-name/$CHUNK_SIZE`
    2. Moves the following files to the newly created directory:
        - eval/model/efficiency/datasets/Warehouse_240219_GoPro_9_GX010002/
        chat/ground_truth_qa_1.json.result.csv
        - out.log
        - /tmp/via-logs/via_engine.log
        - /tmp/via-logs/*$req_id*
    """
    # Extract the video-file-name from the MEDIA_PATH
    video_file_name = os.path.basename(MEDIA_PATH)
    accuracy_results = {}
    score_vlm = None  # Initialize score_vlm to avoid undefined variable error
    # Create the directory
    if VLM == "openai-compat":
        name = os.environ["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"]
        unique_test_code = f"{name}_{video_file_name}_{CHUNK_SIZE}_{req_id}"
    else:
        unique_test_code = f"{VLM}_{video_file_name}_{CHUNK_SIZE}_{req_id}"
    dir_path = f"logs/accuracy/{unique_test_code}"
    print("copying results to", str(dir_path))
    os.makedirs(dir_path, exist_ok=True)

    dump_dict_to_json(test_data, dir_path + "/input_test_config.json")

    # Move the files
    files_to_move = [
        GROUND_TRUTH_FILE + ".result.csv",
    ]
    files_to_copy = [
        "out.log",
        "/tmp/via-logs/via_engine.log",
        "/tmp/via-logs/via_ctx_rag.log",
        f"/tmp/via/cached_frames/{req_id}/{req_id}.mp4",
    ]
    for file in files_to_move:
        try:
            shutil.move(file, dir_path)
        except FileNotFoundError:
            print("File not found: {}; Skipping;".format(file))

    for file in files_to_copy:
        try:
            shutil.copy(file, dir_path)
        except FileNotFoundError:
            print("File not found: {}; Skipping;".format(file))
        except IOError as e:
            print(f"Error copying file {file}: {e}; Skipping;")

    # Move the files with the wildcard
    wildcard_files = glob.glob(f"/tmp/via-logs/*{req_id}*")
    vlm_dc_out_file = None
    for file in wildcard_files:
        try:
            shutil.move(file, dir_path)
            if os.path.basename(file).startswith("vlm_testdata_"):
                vlm_dc_out_file = os.path.join(dir_path, os.path.basename(file))
        except FileNotFoundError:
            print(f"File not found: {file}")

    try:
        if SUMMARY_GT:
            file = os.path.abspath(SUMMARY_GT)
            shutil.copy(file, os.path.join(dir_path, "summary_gt.txt"))
    except (FileNotFoundError, TypeError):
        print("File not found or invalid path: {}; Skipping;".format(SUMMARY_GT))

    dc_eval_result = None
    if (
        evaluator is not None
        and vlm_dc_out_file is not None
        and PROMPT_1 is not None
        and DC_GT is not None
    ):
        try:
            score_vlm, dc_eval_result = evaluate_dc(vlm_dc_out_file, evaluator, DC_GT, PROMPT_1)
            accuracy_results["score_vlm"] = score_vlm
            print(
                f"DC evaluation completed: score_vlm={score_vlm}, num_scores={len(dc_eval_result)}"
            )
        except Exception as e:
            print(f"Error in DC evaluation: {e}")

        if dc_eval_result is not None:  # Only write CSV if we have valid data
            with open(f"{dir_path}/dc_scores.csv", "w") as dc_scores_file:
                writer = csv.writer(dc_scores_file)
                writer.writerow(["Chunk_ID", "Score", "Reasoning"])
                for chunk_id in dc_eval_result:
                    writer.writerow(
                        [
                            chunk_id,
                            dc_eval_result[chunk_id]["score"],
                            dc_eval_result[chunk_id]["reasoning"],
                        ]
                    )

        print(f"VLM accuracy score for {str(dir_path)}={str(score_vlm)}")
        try:
            if DC_GT:
                file = os.path.abspath(DC_GT)
                shutil.copy(file, os.path.join(dir_path, "dc_gt.txt"))
        except (FileNotFoundError, TypeError):
            print("File not found or invalid path: {}; Skipping;".format(DC_GT))

    accuracy_results["video_id"] = video_id
    accuracy_results["VLM"] = VLM
    accuracy_results["score_summary"] = summary_score
    accuracy_results["score_vlm"] = score_vlm
    accuracy_results["avg_chat_score"] = avg_chat_score
    accuracy_results["unique_test_code"] = unique_test_code

    # Open the file in write mode
    accuracy_results_file_path = f"{dir_path}/accuracy_{req_id}.log"
    with open(accuracy_results_file_path, "w") as f:
        # Write the dictionary to the file as a JSON object
        json.dump(accuracy_results, f)
    return str(unique_test_code)
