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

import requests
import re
import json

from openai import OpenAI


class VSS:
    """Wrapper to call VSS REST APIs"""

    def __init__(self, host):

        self.host = host

        self.summarize_endpoint = self.host + "/summarize"
        self.query_endpoint = self.host + "/chat/completions"
        self.files_endpoint = self.host + "/files"
        self.models_endpoint = self.host + "/models"

        self.model = self.get_model()

        self.f_count = 0

    def check_response(self, response, json_format=True):
        print(f"Response Status Code: {response.status_code}")
        if response.status_code == 200:
            print("Request Status: SUCCESS")
            if json_format:
                return response.json()
            else:
                return response.text
        else:
            print("Request Status: ERROR")
            print(response.text)
            raise Exception(
                f"VSS Request Failed: {response}\n{response.status_code}\n{response.text}"
            )

    def get_model(self):
        response = requests.get(self.models_endpoint)
        json_data = self.check_response(response)
        return json_data["data"][0]["id"]  # get configured model name

    def upload_video(self, video_path):
        files = {"file": (f"file_{self.f_count}", open(video_path, "rb"))}
        data = {"purpose": "vision", "media_type": "video"}
        response = requests.post(self.files_endpoint, data=data, files=files)
        self.f_count += 1
        json_data = self.check_response(response)
        return json_data.get("id")  # return uploaded file id

    def summarize_video(self, file_id, prompt, cs_prompt, sa_prompt, chunk_duration):
        body = {
            "id": file_id,
            "prompt": prompt,
            "caption_summarization_prompt": cs_prompt,
            "summary_aggregation_prompt": sa_prompt,
            "model": self.model,
            "chunk_duration": chunk_duration,
            "enable_chat": True,
        }

        response = requests.post(self.summarize_endpoint, json=body)

        # check response
        json_data = self.check_response(response)
        message_content = json_data["choices"][0]["message"]["content"]
        return message_content

    def query_video(self, file_id, query):
        body = {
            "id": file_id,
            "messages": [{"content": query, "role": "user"}],
            "model": self.model,
        }
        response = requests.post(self.query_endpoint, json=body)
        json_data = self.check_response(response)
        message_content = json_data["choices"][0]["message"]["content"]
        return message_content


class PromptWizard:
    """Use an LLM to auto generate prompts and questions for VSS based on a high level goal. Requires LLM to output JSON format."""

    def __init__(self, goal, model, api_key=None, base_url=None):
        self.goal = goal
        self.model = model

        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.extraction_pattern = r'"([^"]*?)"'

        self._questions = None
        self._prompt = None
        self._caption_summarization_prompt = None
        self._summary_aggregation_prompt = None

    def llm(self, system_prompt):
        response = self.client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": self.goal,
                },
            ],
            model=self.model,
        )

        response = response.choices[0].message.content
        return response

    @property
    def questions(self):
        if self._questions:
            return self._questions
        system_prompt = "Your job is to generate a list of questions that can be used to help achieve the user's goal. These questions will be asked to a video agent that fully understands the content of a video. Respond in JSON format with the key as 'questions' and the value a list of strings where each element is a question.}"
        response = self.llm(system_prompt)
        # Try to find json code block
        re_search = re.search(r"```json\n(.*?)\n```", response, re.DOTALL)
        if re_search:
            json_string = re_search.group(1)
        # If no code block then find curly braces
        else:
            left_index = response.find("{")
            right_index = response.rfind("}")
            json_string = response[left_index : right_index + 1]

        questions = json.loads(json_string)["questions"]
        self._questions = questions
        return self._questions

    @property
    def prompt(self):
        if self._prompt:
            return self._prompt
        system_prompt = "Your job is to assist the user in creating a prompt that will be given to a vision language model. The vision language model is capable of taking in images and a text prompt and returning a text response. You need to come up with a prompt that can be given to the vision language model so it knows what to look for in the image based on what the user is asking for. The suggested prompt MUST be wrapped in double quotes. Do not use quotes in any other way. You must suggest only ONE prompt wrapped in double quotes."
        response = self.llm(system_prompt)
        print(response)
        matches = re.findall(self.extraction_pattern, response)
        if len(matches) == 0:
            raise Exception(
                "Failed to extract prompt from LLM response. Try again or use a different LLM."
            )
        prompt = matches[0]
        self._prompt = prompt
        return self._prompt

    @property
    def caption_summarization_prompt(self):
        if self._caption_summarization_prompt:
            return self._caption_summarization_prompt
        self._caption_summarization_prompt = "You will be provided several descriptions based on a continuous video file. Combine the descriptions if there is any overlapping information"
        return self._caption_summarization_prompt

    @property
    def summary_aggregation_prompt(self):
        if self._summary_aggregation_prompt:
            return self._summary_aggregation_prompt
        system_prompt = "Your job is to assist the user in creating a prompt that will be given to a large language model along with a set of text descriptions that describe a video. Your prompt needs to instruct the large language model what to do with the descriptions and how to format them based on what the user wants. Output the suggested prompt between quotes."
        response = self.llm(system_prompt)
        matches = re.findall(self.extraction_pattern, response)
        if len(matches) == 0:
            raise Exception(
                "Failed to extract prompt from LLM response. Try again or use a different LLM."
            )
        summary_aggregation_prompt = matches[0]
        self._summary_aggregation_prompt = summary_aggregation_prompt
        return self._summary_aggregation_prompt


if __name__ == "__main__":
    # Example Usage

    # configure model, base_url and api_key based on your desired LLM. Could be locally hosted, cloud or gpt4.
    wizard = PromptWizard(
        "I want a traffic report that includes car crashes, emergency vehicles and other important details.",
        model="meta/llama-3.1-70b-instruct",
        base_url="http://0.0.0.0:8000/v1",
        api_key="abc123",
    )

    # prompt wizard will output the three required prompts for VSS based on the high level goal provided to it.
    print(wizard.prompt)
    print()
    print(wizard.caption_summarization_prompt)
    print()
    print(wizard.summary_aggregation_prompt)
    print()
    print(wizard.questions)

    # connect to VSS. Configure IP
    vss = VSS(
        "http://0.0.0.0:8100",
    )

    # upload video. Adjust path to test video.
    video_id = vss.upload_video("test_video.mp4")
    print(f"{video_id=}")

    # summarize video using the auto generated prompts
    summary = vss.summarize_video(
        video_id,
        wizard.prompt,
        wizard.caption_summarization_prompt,
        wizard.summary_aggregation_prompt,
        20,  # chunk size
    )
    print(f"{summary=}")

    # Prompt wizard can also auto generate questions based on the goal. We can ask these questions directly to VSS after video summarization.
    qa = {"questions": [], "answers": []}
    for question in wizard.questions:
        answer = vss.query_video(video_id, question)
        qa["questions"].append(question)
        qa["answers"].append(answer)

    # print Q&A
    for q, a in zip(qa["questions"], qa["answers"]):
        print(f"Question: {q} \n Answer: {a}\n")
