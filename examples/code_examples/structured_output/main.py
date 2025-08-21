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


import gradio as gr
import argparse
from vss import VSS, PromptWizard
from openai import OpenAI
import re
import json
import os

api_key_g = None
base_url_g = None
vss_host_g = None

# defaults
default_goal = "I want to fill out a car insurance claim based on a short video of a damaged vehicle."
default_fields = [
    ["Car Type", "Choices: Truck, SUV, Sedan"],
    ["Color", ""],
    ["Tire Damage", ""],
    ["Windshield Damage", ""],
    ["Window Damage", ""],
    ["Body Damage", ""],
    ["Overall Condition", ""],
]


# wrapper for llm calling
def llm_call(model, user_prompt, system_prompt):
    client = OpenAI(base_url=base_url_g, api_key=api_key_g)
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": user_prompt,
        },
    ]
    completion = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.2,
        top_p=0.7,
        max_tokens=4096,
        stream=False,
    )

    llm_response = completion.choices[0].message.content
    return llm_response


def extract_fields(model, goal, structured_fields, video_input):
    field_names = [x[0] for x in structured_fields]
    field_descriptions = [x[1] for x in structured_fields]
    fields = {}
    for x in range(len(field_names)):
        fields[field_names[x]] = field_descriptions[x]

    # turn structured fields into high level goal

    # create form wizard to generate prompts and questions
    wizard = PromptWizard(
        f"{goal} \n Additionally I want to fill out a form based on the video. I want you to keep this form in mind while coming up with the prompt. Think through what details need to be known in order to fill out this form based on an input video. Create the prompts while taking these into account. The fields are formatted as a dictionary with the key being the field name and the value being an optional description of the field. Here are the fields: {fields}",
        model,
        api_key=api_key_g,
        base_url=base_url_g,
    )
    # call VSS with prompts
    vss = VSS(
        vss_host_g,
    )
    video_id = vss.upload_video(video_input)
    prompt_l = wizard.prompt
    cs_prompt_l = wizard.caption_summarization_prompt
    sa_prompt_l = wizard.summary_aggregation_prompt
    print(f"Prompt: {prompt_l}")
    print(f"cs_prompt: {cs_prompt_l}")
    print(f"sa_prompt: {sa_prompt_l}")
    summary = vss.summarize_video(
        video_id,
        prompt_l,
        cs_prompt_l,
        sa_prompt_l,
        20,  # chunk size
    )
    print(f"Summary: {summary}")

    # call Q&A on questions
    qa = {"questions": [], "answers": []}
    for question in wizard.questions:
        answer = vss.query_video(video_id, question)
        qa["questions"].append(question)
        qa["answers"].append(answer)

    # combine summary and Q&A
    llm_user_prompt = f"The user is trying to achieve the follow goal on an input video: {goal}. A separate agent has generated a video summary and set of question an answers about the video.  VIDEO SUMMARY: {summary}\n VIDEO Q&A: {qa}\n You need to fill out a form with the following field name and descriptions: {fields}. Return a json block where the key is the field name and value is the answer to the field based on the video summary and question, answer pairs. If the field value cannot be determined then say Unknown. Do not make anything up."
    llm_sys_prompt = f"You will be given a summary of a video along with follow question answer pairs about the video. Use these details to fill out a form. The fields are formatted as a dictionary with the key being the field name and the value being an optional description of the field. Your final output should be a json block where each key is a field name and each value is the associated data for the form field. You must use the exact field names provided. Do not add any extra fields. All values should be a single string. Do not add nested fields. Do not use any special formatting characters within the JSON."
    # call llm to fill form
    form_response = llm_call(
        model,
        llm_user_prompt,
        llm_sys_prompt,
    )
    print(form_response)

    # extract json response
    try:
        # Try to find json code block
        re_search = re.search(r"```json\n(.*?)\n```", form_response, re.DOTALL)
        if re_search:
            json_string = re_search.group(1)
        # If no code block then find curly braces
        else:
            left_index = form_response.find("{")
            right_index = form_response.rfind("}")
            json_string = form_response[left_index : right_index + 1]

        json_object = json.loads(json_string)
    except Exception as e:
        print(f"JSON Parsing Error: {e}")
        json_object = {
            key: None for key in field_names
        }  # return empty expected dict with no values

    # return form
    response_table = [[key, value] for key, value in json_object.items()]
    return response_table


def main(model_list, port=7860):
    with gr.Blocks() as demo:
        gr.HTML('<h1 style="color: #6aa84f; font-size: 250%;">VSS Form Filling</h1>')
        with gr.Row():

            with gr.Column():
                gr.Markdown("## Describe Usecase")
                goal_tb = gr.Textbox(
                    label="Provide context to the agent about the input video and use case.",
                    value=default_goal,
                )
                gr.Markdown("## Select LLM Model")
                gr.Markdown("Select an LLM to handle form post-processing.")
                llm_selection = gr.Dropdown(
                    choices=model_list,
                    label="LLM Selection",
                    info="LLM to post process the output and ensure it is in JSON format.",
                    value=model_list[0],
                )
            with gr.Column():
                gr.Markdown("## Upload a Video")
                gr.Markdown("Supply an Input Video to extract fields from.")
                video_input = gr.Video()

        with gr.Row():

            with gr.Column():
                gr.Markdown("## Define Form Fields")
                gr.Markdown(
                    "Supply the form fields to extract from the video. Optionally a field description can be added to give the models more context."
                )

                structured_fields = gr.DataFrame(
                    interactive=True,
                    headers=["Field Name", "Field Description"],
                    col_count=(2, "fixed"),
                    type="array",
                    value=default_fields,
                )

            with gr.Column():
                gr.Markdown("## Form Output")
                gr.Markdown("View the fields VSS extracted from the video")
                form_output = gr.DataFrame(
                    interactive=False,
                    type="array",
                    headers=["Field Name", "Extracted Value"],
                )

        submit_btn = gr.Button()

        submit_btn.click(
            fn=extract_fields,
            inputs=[
                llm_selection,
                goal_tb,
                structured_fields,
                video_input,
            ],
            outputs=form_output,
        )

    demo.launch(server_name="0.0.0.0", server_port=port)


if __name__ == "__main__":
    # You MUST export either OPENAI_API_KEY or NVIDIA_API_KEY. If neither then define base_url, api_key and model to point to self hosted or custom model.
    parser = argparse.ArgumentParser(description="Video Report Filling")

    parser.add_argument(
        "vss_host",
        type=str,
        help="URL for VSS Host. Example: 'http://localhost:8000/v1' ",
    )
    parser.add_argument("--api_key", type=str, help="Custom API Key")
    parser.add_argument("--base_url", type=str, help="Custom base url for LLM")
    parser.add_argument(
        "--gradio_port", type=int, default=7860, help="Port to run Gradio UI"
    )
    args = parser.parse_args()

    # handle openAI, NIM & local deployment options.
    if "OPENAI_API_KEY" in os.environ:
        api_key = os.environ["OPENAI_API_KEY"]
        base_url = "https://api.openai.com/v1"

    elif "NVIDIA_API_KEY" in os.environ:
        api_key = os.environ["NVIDIA_API_KEY"]
        base_url = "https://integrate.api.nvidia.com/v1"

    else:
        api_key = args.api_key
        base_url = args.base_url

    client = OpenAI(base_url=base_url, api_key=api_key)
    models = client.models.list()
    model_list = [model.id for model in models]

    # set to globals
    api_key_g = api_key
    base_url_g = base_url
    vss_host_g = args.vss_host

    main(model_list, port=args.gradio_port)
