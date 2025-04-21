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

import atexit
import json
import os
import shutil
import sys
import tempfile
from logging import Logger
from pathlib import Path

import aiohttp
import gradio as gr
import pkg_resources
import yaml
from gradio_videotimeline import VideoTimeline

from utils import MediaFileInfo

STANDALONE_MODE = True
pipeline_args = None
logger: Logger = None
appConfig = {}

DEFAULT_CHUNK_SIZE = 0
DEFAULT_VIA_TARGET_RESPONSE_TIME = 2 * 60  # in seconds
DEFAULT_VIA_TARGET_USECASE_EVENT_DURATION = 10  # in seconds

dummy_mr = """
#### just to create the space
"""
column_names = ["Alert Name", "Event(s) [comma separated]", "", ""]

USER_AVATAR_ICON = tempfile.NamedTemporaryFile()
USER_AVATAR_ICON.write(
    pkg_resources.resource_string("__main__", "client/assets/user-icon-60px.png")
)
USER_AVATAR_ICON.flush()
CHATBOT_AVATAR_ICON = tempfile.NamedTemporaryFile()
CHATBOT_AVATAR_ICON.write(
    pkg_resources.resource_string("__main__", "client/assets/chatbot-icon-60px.png")
)
CHATBOT_AVATAR_ICON.flush()


def LINE():
    return sys._getframe(1).f_lineno


def get_default_prompts():
    try:
        with open(os.environ.get("CA_RAG_CONFIG", "/opt/nvidia/via/default_config.yaml")) as f:
            ca_rag_config = yaml.safe_load(f)
            prompts = ca_rag_config["summarization"]["prompts"]
            return (
                prompts["caption"],
                prompts["caption_summarization"],
                prompts["summary_aggregation"],
                # "/opt/nvidia/via/warehouse_graph_rag_config.yaml",
            )
    except Exception:
        return "", "", "", None


async def enable_button(gallery_data):
    yield (
        gr.update(interactive=True, value="Summarize"),
        gr.update(value=[["", "", "", ""]] * 10),
        [[]],
    )
    return


def remove_icon_files():
    USER_AVATAR_ICON.close()
    CHATBOT_AVATAR_ICON.close()


atexit.register(remove_icon_files)


async def remove_all_media(session: aiohttp.ClientSession, media_ids):
    for media_id in media_ids:
        async with session.delete(appConfig["backend"] + "/files/" + media_id):
            pass


async def add_assets(
    gr_video,
    chatbot,
    image_mode,
    dc_json_path,
    request: gr.Request,
):
    logger.info(f"summarize. ip: {request.client.host}")
    if not gr_video:
        return [
            gr.update(),
        ] * 36
    else:
        url = appConfig["backend"] + "/files"
        session: aiohttp.ClientSession = appConfig["session"]

        media_ids = []
        if image_mode is True:
            media_paths = []
            for tup in gr_video:
                media_paths.append(tup[0])
                async with session.post(
                    url,
                    data={
                        "filename": (None, tup[0]),
                        "purpose": (None, "vision"),
                        "media_type": (None, "image"),
                    },
                ) as resp:
                    resp_json = await resp.json()
                    if resp.status >= 400:
                        chatbot = [[None, "<b>Error: </b><i>" + resp_json["message"] + "</i>"]]
                        await remove_all_media(session, media_ids)
                        return (
                            chatbot,
                            [],
                            *[
                                gr.update(),
                            ]
                            * 34,
                        )
                    media_ids.append(resp_json["id"])
            logger.debug(f"multi-img; media_paths is {str(media_paths)}")

        else:
            media_path = os.path.abspath(gr_video)
            # Copy dense caption json if its present
            enable_dense_caption = bool(os.environ.get("ENABLE_DENSE_CAPTION", False))
            if enable_dense_caption:
                if os.path.exists(dc_json_path):
                    dc_path = media_path + ".dc.json"
                    shutil.copy(dc_json_path, dc_path)

            async with session.post(
                url,
                data={
                    "filename": media_path,
                    "purpose": "vision",
                    "media_type": "video",
                },
            ) as resp:
                resp_json = await resp.json()
                if resp.status >= 400:
                    chatbot = [[None, "<b>Error: </b><i>" + resp_json["message"] + "</i>"]]
                    return (
                        chatbot,
                        [],
                        *[
                            gr.update(),
                        ]
                        * 34,
                    )
                media_ids.append(resp_json["id"])

        chatbot = []
        chatbot = chatbot + [
            [None, "Processing the image(s) ..." if image_mode else "Processing the video ..."]
        ]

        return (
            chatbot,
            media_ids,
            resp,
            *[
                gr.update(interactive=False),
            ]
            * 31,
            gr.update(value=None),
            gr.update(open=False),
        )


def convert_seconds_to_string(seconds, need_hour=False, millisec=False):
    seconds_in = seconds
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)

    if need_hour or hours > 0:
        ret_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    else:
        ret_str = f"{minutes:02d}:{seconds:02d}"

    if millisec:
        ms = int((seconds_in * 100) % 100)
        ret_str += f".{ms:02d}"
    return ret_str


def get_response_table(responses):
    return (
        "<table><thead><th>Duration</th><th>Response</th></thead><tbody>"
        + "".join(
            [
                f'<tr><td>{convert_seconds_to_string(item["media_info"]["start_offset"])} '
                f'-> {convert_seconds_to_string(item["media_info"]["end_offset"])}</td>'
                f'<td>{item["choices"][0]["message"]["content"]}</td></tr>'
                for item in responses
            ]
        )
        + "</tbody></table>"
    )


async def reset_chat(chatbot):
    # Reset all UI components to their initial state
    chatbot = []
    yield (chatbot, gr.update(value=None), gr.update(open=False))
    return


async def close_asset(chatbot, question_textbox, video, media_ids, image_mode):
    session: aiohttp.ClientSession = appConfig["session"]
    await remove_all_media(session, media_ids)
    # Reset all UI components to their initial state
    chatbot = []
    yield (
        chatbot,
        gr.update(interactive=False, value=""),  # question_textbox
        gr.update(interactive=False),  # ask_button
        gr.update(interactive=False),  # reset_chat_button
        gr.update(interactive=False),  # close_asset_button
        gr.update(value=None),  # video
        gr.update(
            interactive=False,
            value=f"Select/Upload {'image(s)' if image_mode else 'video'} to summarize",
        ),  # summarize_button
        gr.update(interactive=True),  # chat_button
        None,  # output_alerts
        gr.update(value=[[""] * 4] * 10, headers=column_names),  # alerts_table,
        gr.update(interactive=True, value=0),  # num_frames_per_chunk
        gr.update(interactive=True, value=0),  # vlm_input_width
        gr.update(interactive=True, value=0),  # vlm_input_height
        gr.update(interactive=True, value=0.4),  # temprature
        gr.update(interactive=True, value=1),  # top_p
        gr.update(interactive=True, value=100),  # top_k,
        gr.update(interactive=True, value=512),  # max_new_tokens
        gr.update(interactive=True, value=1),  # seed
        gr.update(value=None),  # timeline
        gr.update(open=False),  # timeline_accordion
        gr.update(interactive=False),  # generate_highlight
        gr.update(interactive=False),  # generate_scenario_highlight
        gr.update(interactive=True, value=0.7),  # summarize_top_p
        gr.update(interactive=True, value=0.2),  # summarize_temperature
        gr.update(interactive=True, value=2048),  # summarize_max_tokens
        gr.update(interactive=True, value=0.7),  # chat_top_p
        gr.update(interactive=True, value=0.2),  # chat_temperature
        gr.update(interactive=True, value=512),  # chat_max_tokens
        gr.update(interactive=True, value=0.7),  # notification_top_p
        gr.update(interactive=True, value=0.2),  # notification_temperature
        gr.update(interactive=True, value=2048),  # notification_max_tokens
        gr.update(interactive=True, value=6),  # summarize_batch_size
        gr.update(interactive=True, value=1),  # rag_batch_size
        gr.update(interactive=True, value="graph-rag"),  # rag_type
        gr.update(interactive=True, value=5),  # rag_top_k
        gr.update(value=None),  # display_image
        [[]],  # Reset table_state to initial empty state
    )
    return


def string_to_json(input_string):
    """
    Convert a string representation of a dictionary to a JSON string.

    Args:
    input_string (str): A string representation of a dictionary.

    Returns:
    str: A JSON string parsed from the input string, or None if parsing fails.
    """
    try:
        # Replace single quotes with double quotes, except for those within strings
        modified_string = input_string.replace("'", '"').replace('"\\"', "'").replace('\\""', "'")

        # Parse the modified string as JSON
        json_data = json.loads(modified_string)

        # Convert the parsed data back to a JSON string
        json_string = json.dumps(json_data)

        return json_string
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON: {str(e)}")
        return None


async def ask_question(
    question_textbox,
    ask_button,
    reset_chat_button,
    video,
    chatbot,
    media_ids,
    chunk_size,
    temperature,
    seed,
    max_new_tokens,
    top_p,
    top_k,
    num_frames_per_chunk,
    vlm_input_width,
    vlm_input_height,
    highlight=False,  # Add new parameter with default False
):
    logger.debug(f"Question: {question_textbox}")
    session: aiohttp.ClientSession = appConfig["session"]
    # ask_button.interactive = False
    question = question_textbox
    video_id = media_ids
    reset_chat_triggered = True
    ribbon_value = None
    if not question:
        chatbot = chatbot + [[None, "<i>Please enter a question</i>"]]
        yield chatbot, gr.update(), gr.update(), gr.update(), gr.update(
            value=ribbon_value
        ), gr.update(open=False)
        return
    if question != "/clear":
        reset_chat_triggered = False
        chatbot = chatbot + [["<b>" + str(question) + " </b>", None]]
    yield chatbot, gr.update(), gr.update(), gr.update(value="", interactive=False), gr.update(
        value=ribbon_value
    ), gr.update(open=False)
    async with session.get(appConfig["backend"] + "/models") as resp:
        resp_json = await resp.json()
        if resp.status >= 400:
            chatbot = [[None, "<b>Error: </b><i>" + resp_json["message"] + "</i>"]]
            yield chatbot, gr.update(interactive=True), gr.update(interactive=True), gr.update(
                interactive=True
            ), gr.update(value=ribbon_value), gr.update(open=False)
            return

        model = resp_json["data"][0]["id"]

    req_json = {
        "id": video_id,
        "model": model,
        "chunk_duration": chunk_size,
        "temperature": temperature,
        "seed": seed,
        "max_tokens": max_new_tokens,
        "top_p": top_p,
        "top_k": top_k,
        "stream": True,
        "stream_options": {"include_usage": True},
        "highlight": highlight,
    }
    # Not passing VLM specific params like num_frames_per_chunk, vlm_input_width
    req_json["messages"] = [{"content": str(question), "role": "user"}]
    session: aiohttp.ClientSession = appConfig["session"]
    async with session.post(appConfig["backend"] + "/chat/completions", json=req_json) as resp:
        if resp.status >= 400:
            resp_json = await resp.json()
            chatbot = chatbot + [[None, "<b>Error: </b><i>" + resp_json["message"] + "</i>"]]
            yield chatbot, gr.update(interactive=True), gr.update(interactive=True), gr.update(
                interactive=True
            ), gr.update(value=ribbon_value), gr.update(open=False)
            return
        response = await resp.text()
        logger.debug(f"response is {str(response)}")
        accumulated_responses = []
        lines = response.splitlines()
        for line in lines:
            data = line.strip()
            response = json.loads(data)
            if response["choices"]:
                accumulated_responses.append(response)
            if response["usage"]:
                usage = response["usage"]

        logger.debug(f"accumulated_responses: {accumulated_responses} usage: {usage}")

        if len(accumulated_responses) == 1:
            response_str = accumulated_responses[0]["choices"][0]["message"]["content"]
            if len(response_str) > 0 and response_str[0] == "{":
                try:
                    json_resp = json.loads(response_str)
                    if json_resp.get("type") == "highlight":
                        returned_json = json.dumps(json_resp["highlightResponse"])
                        ribbon_value = json.loads(returned_json)
                        response_str = "Find the Highlights Below"
                except json.JSONDecodeError:
                    # If JSON parsing fails, proceed with original behavior
                    pass

        elif len(accumulated_responses) > 1:
            response_str = get_response_table(accumulated_responses)
        else:
            response_str = ""

        response_str = response_str.replace("\\n", "<br>").replace("\n", "<br>")
        if question != "/clear":
            chatbot = chatbot + [[None, response_str]]
        yield chatbot, gr.update(interactive=True), gr.update(
            interactive=not reset_chat_triggered
        ), gr.update(interactive=True), gr.update(value=ribbon_value), gr.update(
            open=ribbon_value is not None
        )
        return


def get_output_string(header, items):
    return "\n--------\n".join([header] + items + [header]) if items else header


async def summarize(
    image_mode,
    gr_video,
    chatbot,
    media_ids,
    chunk_size,
    temperature,
    seed,
    max_new_tokens,
    top_p,
    top_k,
    summary_prompt,
    caption_summarization_prompt,
    summary_aggregation_prompt,
    response_obj,
    summarize_top_p,
    summarize_temperature,
    summarize_max_tokens,
    chat_top_p,
    chat_temperature,
    chat_max_tokens,
    notification_top_p,
    notification_temperature,
    notification_max_tokens,
    summarize_batch_size,
    rag_batch_size,
    rag_type,
    rag_top_k,
    request: gr.Request,
    summarize=None,
    enable_chat=True,
    alerts_table=None,
    enable_cv_metadata=False,
    num_frames_per_chunk=0,
    vlm_input_width=0,
    vlm_input_height=0,
    cv_pipeline_prompt="",
    enable_audio=False,
    enable_chat_history=True,
    graph_rag_prompt_yaml=None,
):
    logger.info(f"summarize. ip: {request.client.host}")
    if gr_video is None:
        yield (
            [
                gr.update(),
            ]
            * 33
        )
        return
    elif gr_video is not None and response_obj and media_ids:
        session: aiohttp.ClientSession = appConfig["session"]
        async with session.get(appConfig["backend"] + "/models") as resp:
            resp_json = await resp.json()
            if resp.status >= 400:
                chatbot = [[None, "<b>Error: </b><i>" + resp_json["message"] + "</i>"]]
                yield (
                    chatbot,
                    *[
                        gr.update(interactive=True),
                    ]
                    * 32,
                )
                return
            model = resp_json["data"][0]["id"]

        req_json = {
            "id": media_ids,
            "model": model,
            "chunk_duration": chunk_size,
            "temperature": temperature,
            "seed": seed,
            "max_tokens": max_new_tokens,
            "top_p": top_p,
            "top_k": top_k,
            "stream": True,
            "stream_options": {"include_usage": True},
            "num_frames_per_chunk": num_frames_per_chunk,
            "vlm_input_width": vlm_input_width,
            "vlm_input_height": vlm_input_height,
            "summarize_top_p": summarize_top_p,
            "summarize_temperature": summarize_temperature,
            "summarize_max_tokens": summarize_max_tokens,
            "chat_top_p": chat_top_p,
            "chat_temperature": chat_temperature,
            "chat_max_tokens": chat_max_tokens,
            "notification_top_p": notification_top_p,
            "notification_temperature": notification_temperature,
            "notification_max_tokens": notification_max_tokens,
            "summarize_batch_size": summarize_batch_size,
            "rag_batch_size": rag_batch_size,
            "rag_type": rag_type,
            "rag_top_k": rag_top_k,
        }
        if summary_prompt:
            req_json["prompt"] = summary_prompt
        if caption_summarization_prompt:
            req_json["caption_summarization_prompt"] = caption_summarization_prompt
        if summary_aggregation_prompt:
            req_json["summary_aggregation_prompt"] = summary_aggregation_prompt
        # if graph_rag_prompt_yaml:
        #     with open(graph_rag_prompt_yaml, "r") as f:
        #         yaml_data = yaml.safe_load(f)
        #     string_data = yaml.dump(yaml_data, default_flow_style=False)
        #     req_json["graph_rag_prompt_yaml"] = string_data
        req_json["enable_chat"] = enable_chat
        req_json["enable_chat_history"] = enable_chat_history
        req_json["enable_cv_metadata"] = enable_cv_metadata
        if cv_pipeline_prompt:
            req_json["cv_pipeline_prompt"] = cv_pipeline_prompt
        req_json["enable_audio"] = enable_audio

        parsed_alerts = []
        accumulated_responses = []
        past_alerts = []
        if parsed_alerts:
            output_alerts = get_output_string(
                "Waiting for new alerts..." if past_alerts else "Waiting for alerts", past_alerts
            )
            yield (
                chatbot,
                output_alerts,
                *[
                    gr.update(),
                ]
                * 31,
            )
        else:
            output_alerts = ""
        collected_alerts = alerts_table[
            alerts_table.apply(lambda row: not row.eq("").any(), axis=1)
        ].to_csv(sep=":", index=False, columns=column_names[:-2], header=False, lineterminator=";")
        logger.debug(f"Collected alerts: {collected_alerts}")
        for alert in collected_alerts.split(";"):
            alert = alert.strip()
            if not alert:
                continue
            try:
                alert_name, events = [word.strip() for word in alert.split(":")]
                assert alert_name
                assert events

                parsed_events = [ev.strip() for ev in events.split(",") if ev.strip()]
                assert parsed_events
            except Exception:
                raise gr.Error(f"Failed to parse alert '{alert}'") from None
            parsed_alerts.append(
                {
                    "type": "alert",
                    "alert": {"name": alert_name, "events": parsed_events},
                }
            )
            logger.debug(f"parsed_alerts: {parsed_alerts}")
        if parsed_alerts:
            req_json["tools"] = parsed_alerts

        async with session.post(appConfig["backend"] + "/summarize", json=req_json) as resp:
            if resp.status >= 400:
                resp_json = await resp.json()
                chatbot = []
                chatbot = chatbot + [[None, "<b>Error: </b><i>" + resp_json["message"] + "</i>"]]
                await remove_all_media(session, media_ids)
                yield (
                    chatbot,
                    *[
                        gr.update(interactive=True),
                    ]
                    * 32,
                )
                return
            while True:
                line = await resp.content.readline()
                if not line:
                    break
                line = line.decode("utf-8")
                if not line.startswith("data: "):
                    continue

                data = line.strip()[6:]

                if data == "[DONE]":
                    break
                response = json.loads(data)
                if response["choices"] and response["choices"][0]["finish_reason"] == "stop":
                    accumulated_responses.append(response)
                if response["usage"]:
                    usage = response["usage"]
                request_id = response["id"]
                if (
                    parsed_alerts
                    and response["choices"]
                    and response["choices"][0]["finish_reason"] == "tool_calls"
                ):
                    alert = response["choices"][0]["message"]["tool_calls"][0]["alert"]
                    alert_str = (
                        f"Alert Name: {alert['name']}\n"
                        f"Detected Events: {', '.join(alert['detectedEvents'])}\n"
                        f"Time: {alert['offset']} seconds\n"
                        f"Details: {alert['details']}\n"
                    )
                    past_alerts = past_alerts[int(len(past_alerts) / 99) :] + (
                        [alert_str] if alert_str else []
                    )
                    output_alerts = get_output_string(
                        "Waiting for new alerts..." if past_alerts else "Waiting for alerts",
                        past_alerts,
                    )
                    yield (
                        chatbot,
                        output_alerts,
                        *[
                            gr.update(),
                        ]
                        * 31,
                    )

        if len(accumulated_responses) == 1:
            response_str = accumulated_responses[0]["choices"][0]["message"]["content"]
        elif len(accumulated_responses) > 1:
            response_str = get_response_table(accumulated_responses)
        else:
            response_str = ""
        if "Summarization failed" in response_str:
            chatbot = [[None, "<i>" + response_str + "</i>"]]
        elif response_str:
            if summarize is True:
                summary_type = "image(s)" if image_mode else "video"
                summary_header = f"<b>Here is a summary of the {summary_type}</b>\n\n"
                chatbot = [[None, summary_header + response_str]]
            else:
                chatbot = [[None, f"<b> {'Image(s)' if image_mode else 'Video'} processed</b>"]]
            if usage:
                start_time = accumulated_responses[0]["media_info"]["start_offset"]
                end_time = accumulated_responses[0]["media_info"]["end_offset"]
                duration = end_time - start_time
                chatbot[0][1] += (
                    f"\n\n<i>Processing Time: {usage['query_processing_time']:.2f} seconds\n"
                    f"{'' if image_mode else f'Stream Duration: {duration:.1f} seconds'}</i>"
                )
        else:
            chatbot = [[None, "<b>No summary was generated for given request</b>"]]

        if os.path.exists(f"/tmp/via/cached_frames/{request_id}/{request_id}.mp4"):
            os.rename(
                f"/tmp/via/cached_frames/{request_id}/{request_id}.mp4",
                f"/tmp/gradio/{request_id}.mp4",
            )
            out_vid = (
                """<b>Here is the preprocessed set-of-marks video</b>"""
                + """<div style="max-width: 600px !important;min-width: 600px !important;">"""  # noqa: E501
                + f"""<video src="./gradio_api/file=/tmp/gradio/{request_id}.mp4" autoplay controls inline  /> """  # noqa: E501
                + """</div>"""  # noqa: E501
            )
            chatbot += [
                [None, out_vid],
            ]
        # await remove_all_media(session, media_ids)

        if parsed_alerts and not past_alerts:
            output_alerts = "No alerts were generated for this input media"

        yield (
            chatbot,
            output_alerts,
            gr.update(interactive=False),
            *[
                gr.update(interactive=True),
            ]
            * 30,
        )
        return
    else:
        yield (
            [
                gr.update(),
            ]
            * 33
        )
        return


CHUNK_SIZES = [
    ("No chunking", 0),
    ("5 sec", 5),
    ("10 sec", 10),
    ("20 sec", 20),
    ("30 sec", 30),
    ("1 min", 60),
    ("2 min", 120),
    ("5 min", 300),
    ("10 min", 600),
    ("20 min", 1200),
    ("30 min", 1800),
]


def validate_example_file(path, is_img=False):
    try:
        if (
            ".dc.json" in str(path)
            or ".prompts.json" in str(path)
            or ".poster.jpg" in str(path)
            # or ".graph_rag.yaml" in str(path)
        ):
            return False
        media_info = MediaFileInfo.get_info(path)
        if media_info.video_codec:
            return bool(media_info.is_image) == bool(is_img)
    except Exception as ex:
        print(ex)
        return False
    return bool(media_info.video_codec)


def get_closest_chunk_size(CHUNK_SIZES, x):
    """
    Returns the integer value from CHUNK_SIZES that is closest to x.

    Args:
        CHUNK_SIZES (list of tuples): A list of tuples containing chunk size labels and values.
        x (int): The target value to find the closest chunk size to.

    Returns:
        int: The integer value from CHUNK_SIZES that is closest to x.
    """
    _, values = zip(*CHUNK_SIZES)  # extract just the values from CHUNK_SIZES
    closest_value = min(values, key=lambda v: abs(v - x))  # find the value closest to x
    return closest_value


async def get_recommended_chunk_size(video_length):
    # In seconds:
    target_response_time = DEFAULT_VIA_TARGET_RESPONSE_TIME
    usecase_event_duration = DEFAULT_VIA_TARGET_USECASE_EVENT_DURATION
    recommended_chunk_size = 0

    session: aiohttp.ClientSession = appConfig["session"]
    async with session.post(
        appConfig["backend"] + "/recommended_config",
        json={
            "video_length": int(video_length),
            "target_response_time": int(target_response_time),
            "usecase_event_duration": int(usecase_event_duration),
        },
    ) as response:
        if response.status < 400:
            # Success response from API:
            resp_json = await response.json()
            recommended_chunk_size = int(resp_json.get("chunk_size"))
        if recommended_chunk_size == 0:
            # API fail to provide non-zero chunk size
            # Choose the largest chunk-size in favor of quick VIA execution
            recommended_chunk_size = video_length
        return get_closest_chunk_size(CHUNK_SIZES, recommended_chunk_size)


async def chat_checkbox_selected(chat_checkbox):
    logger.debug("Chat box state updtaed to {}", chat_checkbox)
    return (
        gr.update(visible=chat_checkbox),
        gr.update(visible=chat_checkbox),
        gr.update(visible=chat_checkbox),
        gr.update(visible=chat_checkbox),
        gr.update(visible=chat_checkbox),
    )


async def video_changed(video, image_mode):
    if video:
        if image_mode:
            new_value = 0
        else:
            video_length = (await MediaFileInfo.get_info_async(video)).video_duration_nsec / (
                1000 * 1000 * 1000
            )
            logger.info(f"Video length: {video_length:.2f} seconds")
            new_value = await get_recommended_chunk_size(video_length)
        return [
            gr.update(interactive=True, value="Summarize"),
            gr.update(value=new_value),
            gr.update(value=[["", "", "", ""]] * 10),
            [[]],  # Reset table_state to initial empty state
        ]
    else:
        return [
            gr.update(
                interactive=False,
                value=f"Select/Upload {'image(s)' if image_mode else 'video'} to summarize",
            ),
            gr.update(value=0),
            gr.update(value=[["", "", "", ""]] * 10),
            [[]],  # Reset table_state to initial empty state
        ]


def get_example_details(f):
    dc_path = str()
    prompt, caption_summarization_prompt, summary_aggregation_prompt = get_default_prompts()

    if Path(str(f) + ".dc.json").exists():
        dc_path = str(f) + ".dc.json"

    # if Path(str(f) + ".graph_rag.yaml").exists():
    #     graph_rag_prompt_yaml = str(f) + ".graph_rag.yaml"

    # set default
    cv_pipeline_prompt = "person . forklift . robot . fire . spill"

    try:
        if Path(str(f) + ".prompts.json").exists():
            with open(str(f) + ".prompts.json") as f:
                prompts = json.load(f)
                prompt = prompts["prompt"]
                caption_summarization_prompt = prompts["caption_summarization_prompt"]
                summary_aggregation_prompt = prompts["summary_aggregation_prompt"]
                cv_pipeline_prompt = prompts["cv_pipeline_prompt"]
    except Exception:
        pass

    return (
        dc_path,
        prompt,
        caption_summarization_prompt,
        summary_aggregation_prompt,
        cv_pipeline_prompt,
        # graph_rag_prompt_yaml,
    )


def get_display_image(f, image_mode):
    if image_mode:
        return f
    elif os.path.exists(str(f) + ".poster.jpg"):
        return str(f) + ".poster.jpg"
    else:
        return "/opt/nvidia/via/via-engine/client/assets/chatbot-icon-60px.png"


def build_summarization(args, app_cfg, logger_):
    global appConfig, logger, pipeline_args
    appConfig = app_cfg
    logger = logger_
    pipeline_args = args

    (
        default_prompt,
        default_caption_summarization_prompt,
        default_summary_aggregation_prompt,
        # default_graph_rag_yaml,
    ) = get_default_prompts()

    with open(os.environ.get("CA_RAG_CONFIG", "/opt/nvidia/via/default_config.yaml")) as f:
        ca_rag_config = yaml.safe_load(f)

    media_ids = gr.State("")
    response_obj = gr.State(None)
    popup_visible = gr.State(False)  # State to manage popup visibility

    with gr.Row(equal_height=False):
        with gr.Column(scale=1):
            if args.image_mode is False:
                video = gr.Video(
                    autoplay=True,
                    elem_classes=["white-background", "summary-video"],
                    sources=["upload"],
                    show_download_button=False,
                )
            else:
                video = gr.Gallery(show_label=False, type="filepath")
                chunk_size = gr.State(0)
            display_image = gr.Image(visible=False, type="filepath")

            stream_name = gr.Textbox(show_label=False, visible=False)
            stream_full_path = gr.Textbox(show_label=False, visible=False)
            dc_json_path = gr.Textbox(show_label=False, visible=False)

            with gr.Tabs(elem_id="sub-tabs"):
                with gr.Tab("Prompt"):
                    if args.image_mode is False:
                        chunk_size = gr.Dropdown(
                            choices=CHUNK_SIZES,
                            label="CHUNK SIZE",
                            value=DEFAULT_CHUNK_SIZE,
                            interactive=True,
                            visible=True,
                            elem_classes=["white-background", "bold-header"],
                        )
                    with gr.Accordion(
                        label="PROMPT (OPTIONAL)",
                        elem_classes=["white-background", "bold-header"],
                        open=True,
                    ):
                        summary_prompt = gr.TextArea(
                            label="PROMPT (OPTIONAL)",
                            elem_classes=["white-background", "bold-header"],
                            lines=3,
                            max_lines=3,
                            value=default_prompt,
                            show_label=False,
                        )
                    with gr.Accordion(
                        label="CAPTION SUMMARIZATION PROMPT (OPTIONAL)",
                        elem_classes=["white-background", "bold-header"],
                        open=False,
                    ):
                        caption_summarization_prompt = gr.TextArea(
                            label="CAPTION SUMMARIZATION PROMPT (OPTIONAL)",
                            elem_classes=["white-background", "bold-header"],
                            lines=3,
                            max_lines=3,
                            value=default_caption_summarization_prompt,
                            show_label=False,
                        )
                    with gr.Accordion(
                        label="SUMMARY AGGREGATION PROMPT (OPTIONAL)",
                        elem_classes=["white-background", "bold-header"],
                        open=False,
                    ):
                        summary_aggregation_prompt = gr.TextArea(
                            label="SUMMARY AGGREGATION PROMPT (OPTIONAL)",
                            elem_classes=["white-background", "bold-header"],
                            lines=3,
                            max_lines=3,
                            value=default_summary_aggregation_prompt,
                            show_label=False,
                        )
                    # with gr.Row():
                    #     gr.Markdown("## Upload GraphRAG Prompt config yaml file")
                    #     graph_rag_prompt_yaml = gr.File(
                    #         type="filepath", file_count="single", value=default_graph_rag_yaml
                    #     )  # , file_types=['.yaml', '.yml'])

                    with gr.Row(equal_height=True):
                        gr.Markdown(dummy_mr, visible=True)

                    with gr.Accordion(
                        label="FILE SETTINGS",
                        elem_classes=["white-background", "bold-header"],
                        open=True,
                    ):
                        chat_checkbox = gr.Checkbox(value=True, label="Enable Chat for the file")

                        chat_history_checkbox = gr.Checkbox(value=True, label="Enable chat history")

                        enable_audio = gr.Checkbox(
                            value=False,
                            label="Enable Audio",
                            visible=not args.image_mode
                            and bool(os.environ.get("ENABLE_AUDIO", "false").lower() == "true"),
                        )

                        enable_cv_metadata = gr.Checkbox(
                            value=False,
                            label="Enable CV Metadata",
                            visible=not args.image_mode
                            and bool(
                                os.environ.get("DISABLE_CV_PIPELINE", "true").lower() == "false"
                            ),
                        )

                        cv_pipeline_prompt = gr.TextArea(
                            label="CV PIPELINE PROMPT (OPTIONAL)",
                            lines=1,
                            max_lines=1,
                            value="person . forklift . robot . fire . spill ",
                            visible=not args.image_mode
                            and bool(
                                os.environ.get("DISABLE_CV_PIPELINE", "true").lower() == "false"
                            ),
                        )

                with gr.Tab("Samples"):
                    gr.Examples(
                        examples=[
                            [
                                get_display_image(f, args.image_mode),
                                f.stem,
                                f,
                                *get_example_details(f),
                            ]
                            for f in sorted(Path(args.examples_streams_directory).glob("*"))
                            if f.is_file() and validate_example_file(f.absolute(), args.image_mode)
                        ],
                        inputs=[
                            display_image,
                            stream_name,
                            stream_full_path,
                            dc_json_path,
                            summary_prompt,
                            caption_summarization_prompt,
                            summary_aggregation_prompt,
                            cv_pipeline_prompt,
                            # graph_rag_prompt_yaml,
                        ],
                        label="SELECT A SAMPLE",
                        elem_id="example",
                    )

                with gr.Tab("Create Alerts"):

                    with gr.Row():
                        add_alert_btn = gr.Button(
                            "Add Alert", size="sm", interactive=True, variant="primary"
                        )

                    alerts_table = gr.Dataframe(
                        headers=column_names,
                        datatype=["str", "str", "str", "str"],
                        row_count=10,
                        col_count=(4, "fixed"),
                        interactive=False,
                        elem_classes=["white-background", "alerts-table"],
                    )

                    # Hidden elements for the popup
                    with gr.Column(visible=False, elem_classes="popup") as popup:
                        popup_title = gr.Markdown("### Add New Alert")  # Dynamic title
                        alert_name = gr.Textbox(
                            label="Alert Name",
                            placeholder="Enter alert name...",
                            elem_classes=["white-background"],
                        )
                        alert_events = gr.Textbox(
                            label="Event(s)",
                            placeholder="Enter comma-separated events...",
                            elem_classes=["white-background"],
                        )
                        with gr.Row():
                            cancel_btn = gr.Button("Cancel", size="sm")
                            save_btn = gr.Button(
                                "Save", size="sm", variant="primary", interactive=False
                            )

                    # State to store table data
                    table_state = gr.State([[]])
                    edit_index = gr.State(None)

                    def show_popup(is_edit=False):
                        return [
                            gr.update(visible=True),  # popup
                            gr.update(interactive=False),  # add_alert_btn
                            gr.update(
                                value="### Edit Alert" if is_edit else "### Add New Alert"
                            ),  # popup_title
                        ]

                    def hide_popup():
                        return [
                            gr.update(visible=False),  # popup
                            gr.update(interactive=True),  # add_alert_btn
                            gr.update(value=""),  # alert_name
                            gr.update(value=""),  # alert_events
                            None,  # edit_index
                            gr.update(value="### Add New Alert"),  # popup_title
                        ]

                    def show_edit_popup(evt: gr.SelectData, current_data):
                        try:
                            row_idx = int(evt.index[0])
                            col_idx = int(evt.index[1])
                            logger.debug(f"row_idx: {row_idx}, col_idx: {col_idx}")

                            # Only process if we have data and click is in edit/delete columns
                            if (
                                current_data[0]
                                and row_idx < len(current_data[0])
                                and col_idx in [2, 3]
                            ):  # Edit or Delete columns

                                if col_idx == 2 and evt.value == "Edit":  # Edit column
                                    row_data = current_data[0][row_idx]
                                    return [
                                        gr.update(visible=True),  # popup
                                        gr.update(interactive=False),  # add_alert_btn
                                        gr.update(value=row_data[0]),  # alert_name
                                        gr.update(value=row_data[1]),  # alert_events
                                        row_idx,  # edit_index
                                        gr.update(value="### Edit Alert"),  # popup_title
                                    ]
                        except (IndexError, ValueError, TypeError) as e:
                            logger.error(f"Error in show_edit_popup: {e}")
                            pass

                        return [
                            gr.update(),
                            gr.update(),
                            gr.update(),
                            gr.update(),
                            None,
                            gr.update(),
                        ]

                    def save_alert(alert_name, alert_events, current_data, edit_idx):
                        new_row = [alert_name, alert_events, "Edit", "X"]
                        updated_data = current_data[0].copy() if current_data[0] else []

                        if edit_idx is not None:
                            # Editing existing row
                            updated_data[edit_idx] = new_row
                        else:
                            # Adding new row
                            updated_data.append(new_row)

                        return [updated_data], gr.update(value=updated_data, headers=column_names)

                    def delete_row(evt: gr.SelectData, current_data):
                        try:
                            row_idx = int(evt.index[0])
                            col_idx = int(evt.index[1])
                            logger.debug(f"row_idx: {row_idx}, col_idx: {col_idx}")

                            # Only process if we have data and click is in delete column
                            if (
                                current_data[0]
                                and row_idx < len(current_data[0])
                                and col_idx == 3
                                and evt.value == "X"
                            ):

                                updated_data = (
                                    current_data[0][:row_idx] + current_data[0][row_idx + 1 :]
                                )
                                return [updated_data], gr.update(
                                    value=updated_data, headers=column_names
                                )
                        except (IndexError, ValueError, TypeError) as e:
                            logger.error(f"Error in delete_row: {e}")
                            pass

                        return current_data, gr.update()

                    # Event handlers
                    add_alert_btn.click(show_popup, outputs=[popup, add_alert_btn, popup_title])

                    cancel_btn.click(
                        hide_popup,
                        outputs=[
                            popup,
                            add_alert_btn,
                            alert_name,
                            alert_events,
                            edit_index,
                            popup_title,
                        ],
                    )

                    def update_save_button(alert_name, alert_events):
                        # Enable save button only if both fields have content
                        return gr.update(interactive=bool(alert_name and alert_events))

                    # Add input handlers to check fields
                    alert_name.change(
                        update_save_button, inputs=[alert_name, alert_events], outputs=[save_btn]
                    )
                    alert_events.change(
                        update_save_button, inputs=[alert_name, alert_events], outputs=[save_btn]
                    )
                    save_btn.click(
                        save_alert,
                        inputs=[alert_name, alert_events, table_state, edit_index],
                        outputs=[table_state, alerts_table],
                    ).success(
                        hide_popup,
                        outputs=[
                            popup,
                            add_alert_btn,
                            alert_name,
                            alert_events,
                            edit_index,
                            popup_title,
                        ],
                    )

                    # Add click handlers for edit and delete
                    alerts_table.select(
                        show_edit_popup,
                        inputs=[table_state],
                        outputs=[
                            popup,
                            add_alert_btn,
                            alert_name,
                            alert_events,
                            edit_index,
                            popup_title,
                        ],
                    )

                    alerts_table.select(
                        delete_row, inputs=[table_state], outputs=[table_state, alerts_table]
                    )

            summarize_button = gr.Button(
                interactive=False,
                value=f"Select/Upload {'image(s)' if args.image_mode else 'video'} to summarize",
                variant="primary",
                size="sm",
                scale=1,
            )

        with gr.Column(scale=3):
            with gr.Row(equal_height=True, elem_classes="align-right-row"):
                close_asset_button = gr.Button(
                    "Delete File",
                    variant="primary",
                    interactive=False,
                    size="sm",
                    scale=0.1,
                    elem_classes=["black-button"],
                )
                parameters_button = gr.Button(
                    "Show Parameters",
                    variant="primary",
                    size="sm",  # Set the button size to small
                    scale=0.1,
                    elem_classes=["small-button"],  # Add custom class
                )
            with gr.Tabs(elem_id="via-tabs"):
                with gr.Tab("CHAT"):
                    chatbot = gr.Chatbot(
                        [],
                        label="RESPONSE",
                        bubble_full_width=False,
                        avatar_images=(USER_AVATAR_ICON.name, CHATBOT_AVATAR_ICON.name),
                        height=550,
                        elem_classes="white-background",
                        group_consecutive_messages=False,
                    )

                    with gr.Accordion(
                        "VIDEO EVENT SUMMARY",
                        open=False,
                        elem_classes=["white-background", "bold-header"],
                        elem_id="video-timeline-container",
                    ) as timeline_accordion:
                        timeline = VideoTimeline(
                            show_label=False, interactive=False, show_download_button=False
                        )

                    with gr.Row(equal_height=True, variant="default"):
                        question_textbox = gr.Textbox(
                            label="Ask a question",
                            interactive=False,
                            scale=3,
                        )
                        with gr.Column(scale=1):
                            ask_button = gr.Button("Ask", interactive=False)
                            with gr.Row():
                                generate_scenario_highlight = gr.Button(
                                    "Generate Scenario Highlight",
                                    interactive=False,
                                    scale=1,
                                    visible=not args.image_mode,
                                )
                                generate_highlight = gr.Button(
                                    "Generate Highlight",
                                    interactive=False,
                                    scale=1,
                                    visible=not args.image_mode,
                                )
                            reset_chat_button = gr.Button("Reset Chat", interactive=False)
                with gr.Tab("ALERTS"):
                    output_alerts = gr.TextArea(
                        interactive=False, max_lines=30, lines=30, show_label=False
                    )

    # Add popup container
    with gr.Column(visible=False, elem_classes="modal-container") as popup_container:
        # Add the close button inside the popup
        with gr.Row(equal_height=True, elem_classes="align-right-row"):
            gr.Markdown("**Parameters**")
            close_popup_button = gr.Button(
                "✖",  # Cross sign
                variant="secondary",
                size="sm",
                elem_classes="close-button",  # Add custom class for styling
            )

        with gr.Row(equal_height=True, elem_classes="align-right-row"):
            # VLM Parameters Accordion
            with gr.Accordion("VLM Parameters", open=True):
                with gr.Row():
                    num_frames_per_chunk = gr.Number(
                        label="num_frames_per_chunk",
                        interactive=True,
                        precision=0,
                        minimum=0,
                        maximum=10,
                        value=0,
                        info=("The number of frames to choose from chunk"),
                        elem_classes="white-background",
                    )
                    vlm_input_width = gr.Number(
                        label="VLM Input Width",
                        interactive=True,
                        precision=0,
                        minimum=0,
                        maximum=4096,
                        value=0,
                        info=("Provide VLM frame's width details"),
                        elem_classes="white-background",
                    )
                    vlm_input_height = gr.Number(
                        label="VLM Input Height",
                        interactive=True,
                        precision=0,
                        minimum=0,
                        maximum=4096,
                        value=0,
                        info=("Provide VLM frame's height details"),
                        elem_classes="white-background",
                    )
                with gr.Row():
                    temperature = gr.Slider(
                        minimum=0,
                        maximum=1,
                        value=0.4,
                        interactive=True,
                        label="Temperature",
                        step=0.05,
                        info=(
                            "The sampling temperature to use for text generation."
                            " The higher the temperature value is, the less deterministic"
                            " the output text will be. It is not recommended to modify both"
                            " temperature and top_p in the same call."
                        ),
                        elem_classes="white-background",
                    )
                    top_p = gr.Slider(
                        minimum=0,
                        maximum=1,
                        value=1,
                        interactive=True,
                        label="Top P",
                        step=0.05,
                        info=(
                            "The top-p sampling mass used for text generation."
                            " The top-p value determines the probability mass that is sampled"
                            " at sampling time. For example, if top_p = 0.2,"
                            " only the most likely"
                            " tokens (summing to 0.2 cumulative probability) will be sampled."
                            " It is not recommended to modify both temperature and top_p in the"
                            " same call."
                        ),
                        elem_classes="white-background",
                    )
                    top_k = gr.Number(
                        label="Top K",
                        interactive=True,
                        precision=0,
                        minimum=1,
                        maximum=1000,
                        value=100,
                        info=(
                            "The number of highest probability vocabulary "
                            "tokens to keep for top-k-filtering"
                        ),
                        elem_classes="white-background",
                    )
                with gr.Row():
                    max_new_tokens = gr.Slider(
                        minimum=1,
                        maximum=1024,
                        value=512,
                        interactive=True,
                        label="Max Tokens",
                        step=1,
                        info=(
                            "The maximum number of tokens to generate in any given call."
                            " Note that the model is not aware of this value,"
                            " and generation will"
                            " simply stop at the number of tokens specified."
                        ),
                        elem_classes="white-background",
                    )
                    seed = gr.Number(
                        label="Seed",
                        interactive=True,
                        precision=0,
                        minimum=1,
                        maximum=2**32 - 1,  # noqa: BLK100
                        value=1,
                        info=(
                            "Seed value to use for sampling. "
                            "Repeated requests with the same seed"
                            " and parameters should return the same result."
                        ),
                        elem_classes="white-background",
                    )

            # RAG Parameters Accordion
        with gr.Row(equal_height=True, elem_classes="align-right-row"):
            with gr.Accordion("RAG Parameters", open=True):
                with gr.Accordion("Summarize Parameters", open=False):
                    summarize_top_p = gr.Slider(
                        minimum=0,
                        maximum=1,
                        value=ca_rag_config["summarization"]["llm"]["top_p"],
                        interactive=True,
                        label="Summarize Top P",
                        step=0.05,
                        info=(
                            "The top-p sampling mass used for summarization."
                            " Determines the probability mass that is sampled."
                        ),
                        elem_classes="white-background",
                    )
                    summarize_temperature = gr.Slider(
                        minimum=0,
                        maximum=1,
                        value=ca_rag_config["summarization"]["llm"]["temperature"],
                        interactive=True,
                        label="Summarize Temperature",
                        step=0.05,
                        info=(
                            "The sampling temperature to use for summarization."
                            " Higher values make the output less deterministic."
                        ),
                        elem_classes="white-background",
                    )
                    summarize_max_tokens = gr.Slider(
                        minimum=1,
                        maximum=10240,
                        value=ca_rag_config["summarization"]["llm"]["max_tokens"],
                        interactive=True,
                        label="Summarize Max Tokens",
                        step=1,
                        info=("The maximum number of tokens to generate for summarization."),
                        elem_classes="white-background",
                    )
                with gr.Accordion("Chat Parameters", open=False):
                    chat_top_p = gr.Slider(
                        minimum=0,
                        maximum=1,
                        value=ca_rag_config["chat"]["llm"]["top_p"],
                        interactive=True,
                        label="Chat Top P",
                        step=0.05,
                        info=(
                            "The top-p sampling mass used for chat."
                            " Determines the probability mass that is sampled."
                        ),
                        elem_classes="white-background",
                    )
                    chat_temperature = gr.Slider(
                        minimum=0,
                        maximum=1,
                        value=ca_rag_config["chat"]["llm"]["temperature"],
                        interactive=True,
                        label="Chat Temperature",
                        step=0.05,
                        info=(
                            "The sampling temperature to use for chat."
                            " Higher values make the output less deterministic."
                        ),
                        elem_classes="white-background",
                    )
                    chat_max_tokens = gr.Slider(
                        minimum=1,
                        maximum=10240,
                        value=ca_rag_config["chat"]["llm"]["max_tokens"],
                        interactive=True,
                        label="Chat Max Tokens",
                        step=1,
                        info=("The maximum number of tokens to generate for chat."),
                        elem_classes="white-background",
                    )
                with gr.Accordion("Alert Parameters", open=False):
                    notification_top_p = gr.Slider(
                        minimum=0,
                        maximum=1,
                        value=ca_rag_config["notification"]["llm"]["top_p"],
                        interactive=True,
                        label="Notification Top P",
                        step=0.05,
                        info=(
                            "The top-p sampling mass used for notifications."
                            " Determines the probability mass that is sampled."
                        ),
                        elem_classes="white-background",
                    )
                    notification_temperature = gr.Slider(
                        minimum=0,
                        maximum=1,
                        value=ca_rag_config["notification"]["llm"]["temperature"],
                        interactive=True,
                        label="Notification Temperature",
                        step=0.05,
                        info=(
                            "The sampling temperature to use for notifications."
                            " Higher values make the output less deterministic."
                        ),
                        elem_classes="white-background",
                    )
                    notification_max_tokens = gr.Slider(
                        minimum=1,
                        maximum=10240,
                        value=ca_rag_config["notification"]["llm"]["max_tokens"],
                        interactive=True,
                        label="Notification Max Tokens",
                        step=1,
                        info=("The maximum number of tokens to generate for notifications."),
                        elem_classes="white-background",
                    )
                with gr.Row():
                    summarize_batch_size = gr.Number(
                        label="Summarize Batch Size",
                        interactive=True,
                        precision=0,
                        minimum=1,
                        maximum=1024,
                        value=ca_rag_config["summarization"]["params"]["batch_size"],
                        info=("Batch size for summarization."),
                        elem_classes="white-background",
                    )
                    rag_type = gr.Dropdown(
                        label="RAG Type",
                        choices=["vector-rag", "graph-rag"],
                        value=ca_rag_config["chat"]["rag"],
                        interactive=True,
                        info=("Type of RAG to use."),
                        elem_classes="white-background",
                        elem_id="rag-type-dropdown",
                    )
                with gr.Row():
                    rag_batch_size = gr.Number(
                        label="RAG Batch Size",
                        interactive=True,
                        precision=0,
                        minimum=1,
                        maximum=1024,
                        value=ca_rag_config["chat"]["params"]["batch_size"],
                        info=("Batch size for RAG processing."),
                        elem_classes="white-background",
                    )
                    rag_top_k = gr.Number(
                        label="RAG Top K",
                        interactive=True,
                        precision=0,
                        minimum=1,
                        maximum=1024,
                        value=ca_rag_config["chat"]["params"]["top_k"],
                        info=(
                            "The number of highest probability vocabulary "
                            "tokens to keep for RAG top-k-filtering."
                        ),
                        elem_classes="white-background",
                    )

    with gr.Column(visible=False, scale=1, elem_classes="scenario-popup") as scenario_container:
        with gr.Row(equal_height=True, elem_classes="align-right-row"):
            gr.Markdown("Enter only keywords that you want to highlight:")
        with gr.Row(equal_height=True):
            keywords_input = gr.Textbox(
                label="Keywords",
                placeholder="Enter keywords separated by commas (e.g. car , person , traffic)",
                lines=2,
                elem_classes="keyword-input",
            )
        with gr.Row(equal_height=True):
            submit_keywords = gr.Button("Generate Scenario Highlights", variant="primary")
        with gr.Row(equal_height=True):
            close_scenario_popup = gr.Button("Close")

    def show_container():
        return {
            scenario_container: gr.update(visible=True),
            keywords_input: gr.update(value=""),
        }

    def hide_container():
        return {
            scenario_container: gr.update(visible=False),
        }

    close_scenario_popup.click(
        fn=hide_container,
        outputs=[scenario_container],
    )

    # Function to toggle the popup
    def toggle_popup(visible):
        return gr.update(visible=not visible), not visible

    # Connect the parameters_button to toggle the popup
    parameters_button.click(
        toggle_popup,
        inputs=[popup_visible],
        outputs=[popup_container, popup_visible],
    )

    # Connect the close_popup_button to close the popup
    close_popup_button.click(
        toggle_popup,
        inputs=[popup_visible],
        outputs=[popup_container, popup_visible],
    )

    # Add event handler for rag_type changes
    def update_chat_history_checkbox(selected_rag_type):
        is_graph_rag = selected_rag_type == "graph-rag"
        return gr.update(value=is_graph_rag, interactive=is_graph_rag)

    rag_type.change(
        fn=update_chat_history_checkbox, inputs=[rag_type], outputs=[chat_history_checkbox]
    )

    ask_button.click(
        ask_question,
        inputs=[
            question_textbox,
            ask_button,
            reset_chat_button,
            video,
            chatbot,
            media_ids,
            chunk_size,
            temperature,
            seed,
            max_new_tokens,
            top_p,
            top_k,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
        ],
        outputs=[
            chatbot,
            ask_button,
            reset_chat_button,
            question_textbox,
            timeline,
            timeline_accordion,
        ],
    )

    reset_chat_button.click(
        ask_question,
        inputs=[
            gr.State("/clear"),
            ask_button,
            reset_chat_button,
            video,
            chatbot,
            media_ids,
            chunk_size,
            temperature,
            seed,
            max_new_tokens,
            top_p,
            top_k,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
        ],
        outputs=[
            chatbot,
            ask_button,
            reset_chat_button,
            question_textbox,
            timeline,
            timeline_accordion,
        ],
    ).then(
        reset_chat,
        inputs=[chatbot],
        outputs=[chatbot, timeline, timeline_accordion],
    )

    summarize_button.click(
        add_assets,
        inputs=[
            video,
            chatbot,
            gr.State(args.image_mode),
            dc_json_path,
        ],
        outputs=[
            chatbot,
            media_ids,
            response_obj,
            summarize_button,
            chat_checkbox,
            video,
            temperature,
            top_p,
            top_k,
            max_new_tokens,
            seed,
            chunk_size,
            summary_prompt,
            caption_summarization_prompt,
            summary_aggregation_prompt,
            enable_cv_metadata,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
            cv_pipeline_prompt,
            enable_audio,
            summarize_top_p,
            summarize_temperature,
            summarize_max_tokens,
            chat_top_p,
            chat_temperature,
            chat_max_tokens,
            notification_top_p,
            notification_temperature,
            notification_max_tokens,
            summarize_batch_size,
            rag_batch_size,
            rag_type,
            rag_top_k,
            timeline,
            timeline_accordion,
        ],
        show_progress=False,
    ).then(
        summarize,
        inputs=[
            gr.State(args.image_mode),
            video,
            chatbot,
            media_ids,
            chunk_size,
            temperature,
            seed,
            max_new_tokens,
            top_p,
            top_k,
            summary_prompt,
            caption_summarization_prompt,
            summary_aggregation_prompt,
            response_obj,
            summarize_top_p,
            summarize_temperature,
            summarize_max_tokens,
            chat_top_p,
            chat_temperature,
            chat_max_tokens,
            notification_top_p,
            notification_temperature,
            notification_max_tokens,
            summarize_batch_size,
            rag_batch_size,
            rag_type,
            rag_top_k,
            gr.State(True),  # summarize
            chat_checkbox,  # enable_chat
            alerts_table,
            enable_cv_metadata,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
            cv_pipeline_prompt,
            enable_audio,
            chat_history_checkbox,
        ],
        outputs=[
            chatbot,
            output_alerts,
            summarize_button,
            chat_checkbox,
            video,
            temperature,
            top_p,
            top_k,
            max_new_tokens,
            seed,
            chunk_size,
            summary_prompt,
            caption_summarization_prompt,
            summary_aggregation_prompt,
            enable_cv_metadata,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
            cv_pipeline_prompt,
            enable_audio,
            summarize_top_p,
            summarize_temperature,
            summarize_max_tokens,
            chat_top_p,
            chat_temperature,
            chat_max_tokens,
            notification_top_p,
            notification_temperature,
            notification_max_tokens,
            summarize_batch_size,
            rag_batch_size,
            rag_type,
            rag_top_k,
        ],
        show_progress=False,
    ).then(
        lambda chat_checkbox: (
            gr.update(interactive=False),
            gr.update(interactive=chat_checkbox),
            gr.update(interactive=chat_checkbox),
            gr.update(interactive=chat_checkbox),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
            gr.update(interactive=True),
        ),
        inputs=[chat_checkbox],
        outputs=[
            chat_checkbox,
            ask_button,
            reset_chat_button,
            question_textbox,
            close_asset_button,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
            temperature,
            top_p,
            top_k,
            max_new_tokens,
            seed,
            generate_scenario_highlight,
            generate_highlight,
            summarize_top_p,
            summarize_temperature,
            summarize_max_tokens,
            chat_top_p,
            chat_temperature,
            chat_max_tokens,
            notification_top_p,
            notification_temperature,
            notification_max_tokens,
            summarize_batch_size,
            rag_batch_size,
            rag_type,
            rag_top_k,
        ],
    )

    chat_checkbox.select(
        chat_checkbox_selected,
        inputs=[chat_checkbox],
        outputs=[
            ask_button,
            reset_chat_button,
            question_textbox,
            generate_highlight,
            generate_scenario_highlight,
        ],
    )

    video.change(
        video_changed,
        inputs=[video, gr.State(args.image_mode)],
        outputs=[summarize_button, chunk_size, alerts_table, table_state],
    )
    video.upload(
        fn=enable_button,
        inputs=[video],
        outputs=[
            summarize_button,
            alerts_table,
            table_state,
        ],
    )

    def on_select_example(selected_images, image, stream_full_path, image_mode):
        if not image:
            return (gr.update(), gr.update(), gr.update())

        selected_images = selected_images or []
        return (
            ((selected_images + [image]) if image_mode else stream_full_path),
            gr.update(interactive=True, value="Summarize"),
            gr.update(value="Ask"),
        )

    display_image.change(
        on_select_example,
        inputs=[video, display_image, stream_full_path, gr.State(args.image_mode)],
        outputs=[video, summarize_button, ask_button],
    )

    close_asset_button.click(
        fn=close_asset,
        inputs=[chatbot, question_textbox, video, media_ids, gr.State(args.image_mode)],
        outputs=[
            chatbot,
            question_textbox,
            ask_button,
            reset_chat_button,
            close_asset_button,
            video,
            summarize_button,
            chat_checkbox,
            output_alerts,
            alerts_table,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
            temperature,
            top_p,
            top_k,
            max_new_tokens,
            seed,
            timeline,
            timeline_accordion,
            generate_highlight,
            generate_scenario_highlight,
            summarize_top_p,
            summarize_temperature,
            summarize_max_tokens,
            chat_top_p,
            chat_temperature,
            chat_max_tokens,
            notification_top_p,
            notification_temperature,
            notification_max_tokens,
            summarize_batch_size,
            rag_batch_size,
            rag_type,
            rag_top_k,
            display_image,
            table_state,
        ],
    )

    generate_highlight.click(
        ask_question,
        inputs=[
            gr.State("Generate Video Highlight"),
            ask_button,
            reset_chat_button,
            video,
            chatbot,
            media_ids,
            chunk_size,
            temperature,
            seed,
            max_new_tokens,
            top_p,
            top_k,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
            gr.State(True),  # highlight=True
        ],
        outputs=[
            chatbot,
            ask_button,
            reset_chat_button,
            question_textbox,
            timeline,
            timeline_accordion,
        ],
    )

    generate_scenario_highlight.click(
        fn=show_container, outputs=[scenario_container, keywords_input]
    )

    submit_keywords.click(
        fn=hide_container,
        outputs=[
            scenario_container,
        ],
    ).then(
        ask_question,
        inputs=[
            keywords_input,  # Use the actual question from textbox
            ask_button,
            reset_chat_button,
            video,
            chatbot,
            media_ids,
            chunk_size,
            temperature,
            seed,
            max_new_tokens,
            top_p,
            top_k,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
            gr.State(True),  # highlight=True
        ],
        outputs=[
            chatbot,
            ask_button,
            reset_chat_button,
            question_textbox,
            timeline,
            timeline_accordion,
        ],
    )
