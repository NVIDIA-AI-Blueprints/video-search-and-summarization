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

"""Prompt constants used by VSS tools."""

VLM_PROMPT_EXAMPLES = [
    "You are a warehouse monitoring system. Describe the events in this warehouse and look for any anomalies. "
    "You are an intelligent traffic system. You must monitor and take note of all traffic related events."
]

VLM_FORMAT_INSTRUCTION = """
DON'T MAKE UP ANYTHING THAT NOT FROM THE VIDEO. DON'T HALLUCINATE ANYTHING. Start and end each caption with the timestamp in pts format(presentation timestamp), for example, " <10.5> event_description <11.5> ".
"""

INIT_SUMMARIZE_PROMPT = {
    "prompt": "Write a concise and clear dense caption for the provided video.",
    "caption_summarization_prompt": "Aggregate the following captions in the format **start_timestamp-end_timestamp**event_description. If any two adjacent end_timestamp1 and start_timestamp2 is within a few tenths of a second, and the event_description creates a continuous scene, merge the captions in the format **start_timestamp1-end_timestamp2**event_description. You MUST make sure the timestamp range enclose the entire event.",
    "summary_aggregation_prompt": "Aggregate the following captions in the format **start_timestamp-end_timestamp**event_description. If any two adjacent end_timestamp1 and start_timestamp2 is within a few tenths of a second, and the event_description creates a continuous scene, merge the captions in the format **start_timestamp1-end_timestamp2**event_description",
}

VIDEO_FRAME_TIMESTAMP_PROMPT = """
Get the timestamp from this image, timestamp format: 2024-05-30T01:41:25.000Z. IMPORTANT: only output the timestamp!
"""

VSS_SUMMARIZE_PROMPT = """
You are an expert in VLM, Vision Language Model and have a deep understanding of how to write a prompt that will be given to a vision language model.
The vision language model is capable of taking in images and a text prompt and returning a text response. You need to come up with a prompt that can be given to the vision language model so it knows what to look for in the image based on what the user is asking for. Return the suggested prompt in quotes. Do not use quotes in any other way.

The user's query is:
{user_query}

The user's intent is:
{user_intent}

For different intents, there are several templates you can follow:

## search:
user_query: "was there a person wearing a black jacket involved in the accident?"
output: "Write a dense caption for the video, focusing on person wearing a black jacket, accident, person involved in the accident"

user_query: "person wearing a red jacket"
output: "Write a dense caption for the video, focusing on person, and the details of the attire"

user_query: "box being dropped"
output: "Write a dense caption for the video, focusing on box, movement of the box, and whether box is being dropped"

## root_cause:
user_query: "what caused the fight?"
output: "Write a dense caption for the video, focusing on the fight and any incidents that could directly lead to a fight.looking for notable interactions, escalations, or disturbances involving individuals who might be involved in the subsequent fight.
  Specifically look for any instances of:
  * Verbal disputes or arguments (describe who is involved, body language).
  * Physical provocations or unwanted touch.
  * Individuals displaying clear signs of anger, frustration, or distress.
  * One individual persistently trying to engage with another who seems unwilling.
  * Gatherings or escalations of tension.
  For each instance, provide:
  * Description of individuals involved (clothing, general appearance).
  * Detailed description of the action/behavior.
  * The reaction of other individuals involved or nearby."

user_query: "There is an explosion on the highway at 01:00. Investigate and report what happened?"
output: "Write a dense caption for the video, focusing on the explosion and any incidents that could have led to an explosion.
Specifically look for:
1.  **Vehicles Involved:** Identify all vehicles visible in the scene. Note their type (e.g., car, truck, tanker), color, and direction of travel.
2.  **Traffic Conditions:** Describe the flow of traffic. Is it free-flowing, congested, or stopped (traffic jam)? Note any sudden stops or changes in traffic speed.
3.  **Initial Incidents:** Look for any collisions, fires (even small ones), spills of liquids (especially from trucks or tankers), or unusual behavior of vehicles.
4.  **Escalation:** If an initial incident is detected, track its progression. Does a fire grow? Does damage to a vehicle worsen? Is there a release of any substance?"

## detailed_report:
user_query: "there are some people chasing each other at 00:09 at camera 3. What happened?"
output: "Write a dense caption for the video, focusing on people chasing each other.
Specifically looking for:
- People involved — including clothing, posture, and identifiable features
- Key actions and interactions — such as who does what, to whom, and in what order
- Location context — where the events take place, including landmarks, environment, and time of day
- Object relationships — such as vehicles, buildings, or signs in proximity to people or actions
- Scene progression — the sequence of events and any escalation or movement
Use full sentences and identify each person or object clearly based on appearance or location. Do not leave out any critical details.

## search:
user_query: "There is a robber seen at camera a_4, 00:05, where is the criminal?"
output: "you are an expert in video surveillance, and write a dense caption for the images from the video, look for instances of people that may be doing something odd, include every persons clothing, appearance behavior, things they are carrying and actions in detail, so that every person is clearly identifiable and an act of robbery is reasoned. Remember, suspect everyone as robbery is a nuanced action. actions can be snatching someone's object, picking up something, etc. Specifically look for any changes in the things people are carrying between different image samples to deduce robbery"

# Output format:
ONLY return the generated prompt, do not include any other text.
Your output:

"""
