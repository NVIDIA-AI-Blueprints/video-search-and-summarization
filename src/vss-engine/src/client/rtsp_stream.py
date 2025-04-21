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

import json
import os
import tempfile
import uuid
from logging import Logger

import aiohttp
import gradio as gr
import pkg_resources
import yaml

from .ui_utils import (
    RetrieveCache,
    get_live_stream_preview_chunks,
    get_overlay_live_stream_preview_chunks,
)

pipeline_args = None
enable_logs = True
logger: Logger = None
appConfig = {}


STANDALONE_MODE = False

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


def get_default_prompts():
    try:
        with open(os.environ.get("CA_RAG_CONFIG", "/opt/nvidia/via/default_config.yaml")) as f:
            ca_rag_config = yaml.safe_load(f)
            prompts = ca_rag_config["summarization"]["prompts"]
            return (
                prompts["caption"],
                prompts["caption_summarization"],
                prompts["summary_aggregation"],
                "/opt/nvidia/via/warehouse_graph_rag_config.yaml",
            )
    except Exception:
        return "", "", "", None


async def summarize_response_async(session: aiohttp.ClientSession, req_json, video_id, enable_chat):
    async with session.post(appConfig["backend"] + "/summarize", json=req_json) as resp:
        if resp.status >= 400:
            raise gr.Error((await resp.json())["message"])

        yield (
            gr.update(interactive=False),  # video, , , , ,
            gr.update(interactive=False),  # username, , , , ,
            gr.update(interactive=False),  # password, , , , ,
            gr.update(interactive=False),  # upload_button
            gr.update(interactive=False),  # enable_chat
            gr.update(interactive=enable_chat),  # ask_button
            gr.update(interactive=enable_chat),  # question_textbox
            video_id,  # stream_id
            "Waiting for first summary...",  # output_response
            "Waiting for alerts...",  # output_alerts
            gr.update(interactive=False),  # chunk_size
            gr.update(interactive=False),  # summary_duration
            gr.update(interactive=False),  # alerts
            gr.update(interactive=False),  # summary_prompt
            gr.update(interactive=False),  # caption_summarization_prompt
            gr.update(interactive=False),  # summary_aggregation_prompt
            gr.update(interactive=False),  # temperature
            gr.update(interactive=False),  # top_p
            gr.update(interactive=False),  # top_k
            gr.update(interactive=False),  # max_new_tokens
            gr.update(interactive=False),  # seed
            gr.update(interactive=False),  # num_frames_per_chunk
            gr.update(interactive=False),  # vlm_input_width
            gr.update(interactive=False),  # vlm_input_height
            gr.update(interactive=False),  # summarize_top_p
            gr.update(interactive=False),  # summarize_temperature
            gr.update(interactive=False),  # summarize_max_tokens
            gr.update(interactive=False),  # chat_top_p
            gr.update(interactive=False),  # chat_temperature
            gr.update(interactive=False),  # chat_max_tokens
            gr.update(interactive=False),  # notification_top_p
            gr.update(interactive=False),  # notification_temperature
            gr.update(interactive=False),  # notification_max_tokens
            gr.update(interactive=False),  # summarize_batch_size
            gr.update(interactive=False),  # rag_batch_size
            gr.update(interactive=False),  # rag_type
            gr.update(interactive=False),  # rag_top_k
            gr.update(interactive=False),  # active_live_streams
            gr.update(interactive=False),  # refresh_list_button
            gr.update(interactive=False),  # reconnect_button
            gr.update(interactive=False),  # enable_cv_metadata
            gr.update(interactive=False),  # cv_pipeline_prompt
            gr.update(interactive=False),  # enable_audio
        )
        past_summaries = []
        past_alerts = []
        have_eos = False

        def get_output_string(header, items):
            return "\n--------\n".join([header] + items + [header]) if items else header

        while True:
            line = await resp.content.readline()
            if not line:
                if have_eos:
                    output_summaries = get_output_string("Live Stream Ended", past_summaries)
                    output_alerts = get_output_string("Live Stream Ended", past_alerts)
                else:
                    output_summaries = get_output_string(
                        "Disconnected from server. Reconnect to get latest summaries",
                        past_summaries,
                    )
                    output_alerts = get_output_string(
                        "Disconnected from server. Reconnect to get alerts", past_alerts
                    )
                break
            line = line.decode("utf-8")
            if not line.startswith("data: "):
                yield [gr.update()] * 43
                continue

            data = line.strip()[6:]

            if data == "[DONE]":
                output_summaries = get_output_string("Live Stream Ended", past_summaries)
                output_alerts = get_output_string("Live Stream Ended", past_alerts)
                break

            try:
                response = json.loads(data)
                if response["choices"][0]["finish_reason"] == "stop":
                    response_str_current = (
                        f'{response["media_info"]["start_timestamp"]}'
                        f' -> {response["media_info"]["end_timestamp"]}\n\n'
                        f'{response["choices"][0]["message"]["content"]}'
                    )
                    past_summaries = (
                        past_summaries[int(len(past_summaries) / 9) :] + [response_str_current]
                        if response_str_current
                        else []
                    )
                if response["choices"][0]["finish_reason"] == "tool_calls":
                    alert = response["choices"][0]["message"]["tool_calls"][0]["alert"]
                    alert_str = (
                        f"Alert Name: {alert['name']}\n"
                        f"Detected Events: {', '.join(alert['detectedEvents'])}\n"
                        f"NTP Time: {alert['ntpTimestamp']}\n"
                        f"Details: {alert['details']}\n"
                    )
                    past_alerts = past_alerts[int(len(past_alerts) / 99) :] + (
                        [alert_str] if alert_str else []
                    )
            except Exception:
                pass

            output_summaries = get_output_string(
                "Waiting for next summary..." if past_summaries else "Waiting for first summary...",
                past_summaries,
            )
            output_alerts = get_output_string(
                "Waiting for new alerts..." if past_alerts else "Waiting for alerts", past_alerts
            )

            yield (
                gr.update(interactive=False),  # video, , , , ,
                gr.update(interactive=False),  # username, , , , ,
                gr.update(interactive=False),  # password, , , , ,
                gr.update(interactive=False),  # upload_button
                gr.update(interactive=False),  # enable_chat
                gr.update(interactive=enable_chat),  # ask_button
                gr.update(interactive=enable_chat),  # question_textbox
                video_id,  # stream_id
                output_summaries,  # output_response
                output_alerts,  # output_alerts
                gr.update(interactive=False),  # chunk_size
                gr.update(interactive=False),  # summary_duration
                gr.update(interactive=False),  # alerts
                gr.update(interactive=False),  # summary_prompt
                gr.update(interactive=False),  # caption_summarization_prompt
                gr.update(interactive=False),  # summary_aggregation_prompt
                gr.update(interactive=False),  # temperature
                gr.update(interactive=False),  # top_p
                gr.update(interactive=False),  # top_k
                gr.update(interactive=False),  # max_new_tokens
                gr.update(interactive=False),  # seed
                gr.update(interactive=False),  # num_frames_per_chunk
                gr.update(interactive=False),  # vlm_input_width
                gr.update(interactive=False),  # vlm_input_height
                gr.update(interactive=False),  # summarize_top_p
                gr.update(interactive=False),  # summarize_temperature
                gr.update(interactive=False),  # summarize_max_tokens
                gr.update(interactive=False),  # chat_top_p
                gr.update(interactive=False),  # chat_temperature
                gr.update(interactive=False),  # chat_max_tokens
                gr.update(interactive=False),  # notification_top_p
                gr.update(interactive=False),  # notification_temperature
                gr.update(interactive=False),  # notification_max_tokens
                gr.update(interactive=False),  # summarize_batch_size
                gr.update(interactive=False),  # rag_batch_size
                gr.update(interactive=False),  # rag_type
                gr.update(interactive=False),  # rag_top_k
                gr.update(interactive=False),  # active_live_streams
                gr.update(interactive=False),  # refresh_list_button
                gr.update(interactive=False),  # reconnect_button
                gr.update(interactive=False),  # enable_cv_metadata
                gr.update(interactive=False),  # cv_pipeline_prompt
                gr.update(interactive=False),  # enable_audio
            )

    yield (
        gr.update(interactive=True),  # video, , , , ,
        gr.update(interactive=True),  # username, , , , ,
        gr.update(interactive=True),  # password, , , , ,
        gr.update(interactive=True),  # upload_button
        gr.update(interactive=True),  # enable_chat
        gr.update(interactive=enable_chat),  # ask_button
        gr.update(interactive=enable_chat),  # question_textbox
        video_id,  # stream_id
        output_summaries,  # output_response
        output_alerts,  # output_alerts
        gr.update(interactive=True),  # chunk_size
        gr.update(interactive=True),  # summary_duration
        gr.update(interactive=True),  # alerts
        gr.update(interactive=True),  # summary_prompt
        gr.update(interactive=True),  # caption_summarization_prompt
        gr.update(interactive=True),  # summary_aggregation_prompt
        gr.update(interactive=True),  # temperature
        gr.update(interactive=True),  # top_p
        gr.update(interactive=True),  # top_k
        gr.update(interactive=True),  # max_new_tokens
        gr.update(interactive=True),  # seed
        gr.update(interactive=True),  # num_frames_per_chunk
        gr.update(interactive=True),  # vlm_input_width
        gr.update(interactive=True),  # vlm_input_height
        gr.update(interactive=True),  # summarize_top_p
        gr.update(interactive=True),  # summarize_temperature
        gr.update(interactive=True),  # summarize_max_tokens
        gr.update(interactive=True),  # chat_top_p
        gr.update(interactive=True),  # chat_temperature
        gr.update(interactive=True),  # chat_max_tokens
        gr.update(interactive=True),  # notification_top_p
        gr.update(interactive=True),  # notification_temperature
        gr.update(interactive=True),  # notification_max_tokens
        gr.update(interactive=True),  # summarize_batch_size
        gr.update(interactive=True),  # rag_batch_size
        gr.update(interactive=True),  # rag_type
        gr.update(interactive=True),  # rag_top_k
        gr.update(
            interactive=False, choices=[], value="< Click Refresh List to fetch active streams >"
        ),  # active_live_streams
        gr.update(interactive=True),  # refresh_list_button
        gr.update(interactive=True),  # reconnect_button
        gr.update(interactive=True),  # enable_cv_metadata
        gr.update(interactive=True),  # cv_pipeline_prompt
        gr.update(interactive=True),  # enable_audio
    )


async def gradio_reset(stream_id, request: gr.Request):
    logger.info(f"gradio_reset. ip: {request.client.host}")

    if stream_id:
        session: aiohttp.ClientSession = appConfig["session"]
        async with session.delete(appConfig["backend"] + "/live-stream/" + stream_id):
            pass

    return (
        gr.update(value=None, interactive=True),  # video,
        gr.update(value=None, interactive=True),  # username, , , , ,
        gr.update(value=None, interactive=True),  # password, , , , ,
        gr.update(interactive=False),  # upload_button,
        gr.update(interactive=True),  # enable_chat
        gr.update(interactive=False),  # ask_button
        gr.update(interactive=False),  # reset_chat_button
        gr.update(interactive=False),  # question_textbox
        "",  # stream_id,
        None,  # output_response,
        None,  # output_alerts
        [],  # chatbot,
        gr.update(interactive=True),  # chunk_size,
        gr.update(interactive=True),  # summary_duration,
        gr.update(value=[[""] * 4] * 10, headers=column_names),  # alerts_table,
        gr.update(interactive=True),  # summary_prompt,
        gr.update(interactive=True),  # caption_summarization_prompt,
        gr.update(interactive=True),  # summary_aggregation_prompt,
        gr.update(interactive=True),  # temperature,
        gr.update(interactive=True),  # top_p,
        gr.update(interactive=True),  # top_k,
        gr.update(interactive=True),  # max_new_tokens,
        gr.update(interactive=True),  # seed,
        gr.update(interactive=True),  # num_frames_per_chunk
        gr.update(interactive=True),  # vlm_input_width
        gr.update(interactive=True),  # vlm_input_height
        gr.update(interactive=True),  # summarize_top_p
        gr.update(interactive=True),  # summarize_temperature
        gr.update(interactive=True),  # summarize_max_tokens
        gr.update(interactive=True),  # chat_top_p
        gr.update(interactive=True),  # chat_temperature
        gr.update(interactive=True),  # chat_max_tokens
        gr.update(interactive=True),  # notification_top_p
        gr.update(interactive=True),  # notification_temperature
        gr.update(interactive=True),  # notification_max_tokens
        gr.update(interactive=True),  # summarize_batch_size
        gr.update(interactive=True),  # rag_batch_size
        gr.update(interactive=True),  # rag_type
        gr.update(interactive=True),  # rag_top_k
        await refresh_active_stream_list(),  # active_live_streams,
        gr.update(interactive=True),  # refresh_list_button,
        gr.update(interactive=False),  # reconnect_button
        gr.update(value=False, interactive=True),  # enable_cv_metadata
        gr.update(interactive=True),  # cv_pipeline_prompt
        gr.update(interactive=True),  # enable_audio
        "",  # live_stream_id_preview
        gr.update(value=None),  # set_marks_video,
        gr.update(interactive=False),  # set_marks_tab
    )


async def reset_chat(chatbot):
    # Reset all UI components to their initial state
    chatbot = []
    yield (chatbot)
    return


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
    num_frames_per_chunk,
    vlm_input_width,
    vlm_input_height,
    max_new_tokens,
    top_p,
    top_k,
):
    logger.debug(f"Question: {question_textbox}")
    session: aiohttp.ClientSession = appConfig["session"]
    # ask_button.interactive = False
    question = question_textbox
    video_id = media_ids
    reset_chat_triggered = True
    if not question:
        chatbot = chatbot + [[None, "<i>Please enter a question</i>"]]
        yield chatbot, gr.update(), gr.update(), gr.update()
        return
    if question != "/clear":
        reset_chat_triggered = False
        chatbot = chatbot + [["<b>" + str(question) + " </b>", None]]

    yield chatbot, gr.update(), gr.update(), gr.update(value="", interactive=False)
    async with session.get(appConfig["backend"] + "/models") as resp:
        resp_json = await resp.json()
        if resp.status >= 400:
            chatbot = [[None, "<b>Error: </b><i>" + resp_json["message"] + "</i>"]]
            yield chatbot, gr.update(interactive=True), gr.update(interactive=True), gr.update(
                interactive=True
            )
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
    }
    # Not passing VLM specific params like num_frames_per_chunk, vlm_input_width
    req_json["messages"] = [{"content": str(question), "role": "user"}]
    session: aiohttp.ClientSession = appConfig["session"]
    async with session.post(appConfig["backend"] + "/chat/completions", json=req_json) as resp:
        if resp.status >= 400:
            resp_json = await resp.json()
            chatbot = []
            chatbot = chatbot + [[None, "<b>Error: </b><i>" + resp_json["message"] + "</i>"]]
            yield chatbot, gr.update(interactive=True), gr.update(interactive=True), gr.update(
                interactive=True
            )
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
        else:
            response_str = ""
        if question != "/clear":
            chatbot = chatbot + [[None, response_str]]

        yield chatbot, gr.update(interactive=True), gr.update(
            interactive=not reset_chat_triggered
        ), gr.update(interactive=True)
        return


async def add_rtsp_stream(
    video,
    username,
    password,
    temperature,
    top_p,
    top_k,
    max_new_tokens,
    seed,
    num_frames_per_chunk,
    vlm_input_width,
    vlm_input_height,
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
    chunk_size,
    summary_duration,
    alerts_table,
    summary_prompt,
    caption_summarization_prompt,
    summary_aggregation_prompt,
    enable_chat,
    enable_cv_metadata,
    cv_pipeline_prompt,
    enable_audio,
    clear,
    enable_chat_history,
    request: gr.Request,
):
    logger.info(f"upload_imgorvideo. ip: {request.client.host}")
    if not video:
        yield [
            gr.update(),
        ] * 45
        return
    elif video:
        video_id = ""
        try:
            session: aiohttp.ClientSession = appConfig["session"]
            async with session.get(appConfig["backend"] + "/models") as resp:
                resp_json = await resp.json()
                if resp.status >= 400:
                    raise gr.Error(resp_json["message"])
                model = resp_json["data"][0]["id"]

            req_json = {
                "liveStreamUrl": video.strip(),
                "username": username,
                "password": password,
                "description": "Added from Gradio UI",
            }
            async with session.post(appConfig["backend"] + "/live-stream", json=req_json) as resp:
                resp_json = await resp.json()
                if resp.status != 200:
                    raise gr.Error(resp_json["message"].replace("\\'", "'"))
                video_id = resp_json["id"]

            req_json = {
                "id": video_id,
                "model": model,
                "chunk_duration": chunk_size,
                "summary_duration": summary_duration,
                "temperature": temperature,
                "seed": seed,
                "max_tokens": max_new_tokens,
                "top_p": top_p,
                "top_k": top_k,
                "stream": True,
                "enable_chat": enable_chat,
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
                "enable_cv_metadata": enable_cv_metadata,
                "enable_audio": enable_audio,
                "enable_chat_history": enable_chat_history,
            }
            if summary_prompt:
                req_json["prompt"] = summary_prompt
            if caption_summarization_prompt:
                req_json["caption_summarization_prompt"] = caption_summarization_prompt
            if summary_aggregation_prompt:
                req_json["summary_aggregation_prompt"] = summary_aggregation_prompt
            if cv_pipeline_prompt:
                req_json["cv_pipeline_prompt"] = cv_pipeline_prompt

            parsed_alerts = []
            logger.debug(f"Collected columns: {column_names[:-2]}")
            collected_alerts = alerts_table[
                alerts_table.apply(lambda row: not row.eq("").any(), axis=1)
            ].to_csv(
                sep=":", index=False, columns=column_names[:-2], header=False, lineterminator=";"
            )
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

            yield [
                gr.update(),
            ] * 43 + [
                video_id
            ] + [gr.update(interactive=True)]

            async for response in summarize_response_async(
                session, req_json, video_id, enable_chat
            ):
                yield tuple(response) + (gr.update(),) + (
                    gr.update(interactive=True),
                )  # live_stream_id_preview and clear

        except Exception as ex:
            yield (
                gr.update(interactive=True),  # video, , , , ,
                gr.update(interactive=True),  # username, , , , ,
                gr.update(interactive=True),  # password, , , , ,
                gr.update(interactive=True),  # upload_button
                gr.update(interactive=True),  # enable_chat
                gr.update(interactive=enable_chat),  # ask_button
                gr.update(interactive=enable_chat),  # question_textbox
                video_id,  # stream_id
                "ERROR: " + ex.args[0],  # output_response
                "ERROR: " + ex.args[0],  # output_alerts
                gr.update(interactive=True),  # chunk_size
                gr.update(interactive=True),  # summary_duration
                gr.update(interactive=True),  # alerts_table
                gr.update(interactive=True),  # summary_prompt
                gr.update(interactive=True),  # caption_summarization_prompt
                gr.update(interactive=True),  # summary_aggregation_prompt
                gr.update(interactive=True),  # temperature
                gr.update(interactive=True),  # top_p
                gr.update(interactive=True),  # top_k
                gr.update(interactive=True),  # max_new_tokens
                gr.update(interactive=True),  # seed
                gr.update(interactive=True),  # num_frames_per_chunk
                gr.update(interactive=True),  # vlm_input_width
                gr.update(interactive=True),  # vlm_input_height
                gr.update(interactive=True),  # summarize_top_p
                gr.update(interactive=True),  # summarize_temperature
                gr.update(interactive=True),  # summarize_max_tokens
                gr.update(interactive=True),  # chat_top_p
                gr.update(interactive=True),  # chat_temperature
                gr.update(interactive=True),  # chat_max_tokens
                gr.update(interactive=True),  # notification_top_p
                gr.update(interactive=True),  # notification_temperature
                gr.update(interactive=True),  # notification_max_tokens
                gr.update(interactive=True),  # summarize_batch_size
                gr.update(interactive=True),  # rag_batch_size
                gr.update(interactive=True),  # rag_type
                gr.update(interactive=True),  # rag_top_k
                gr.update(interactive=True),  # active_live_streams
                gr.update(interactive=True),  # refresh_list_button
                gr.update(interactive=True),  # reconnect_button
                gr.update(interactive=True),  # enable_cv_metadata
                gr.update(interactive=True),  # cv_pipeline_prompt
                gr.update(interactive=True),  # enable_audio
                gr.update(""),  # live_stream_id_preview
                gr.update(interactive=True),  # clear
            )
    else:
        raise gr.Error("Only a single input is supported")


CHUNK_SIZES = [
    ("10 sec", 10),
    ("20 sec", 20),
    ("30 sec", 30),
    ("1 min", 60),
    ("2 min", 120),
    ("5 min", 300),
]

SUMMARY_DURATION = [
    ("1 min", 60),
    ("2 min", 120),
    ("5 min", 300),
    ("10 min", 600),
    ("30 min", 1800),
    ("1 hr", 3600),
    ("Till EOS", -1),
]


async def refresh_active_stream_list():
    async with aiohttp.ClientSession() as session:
        async with session.get(appConfig["backend"] + "/live-stream") as resp:
            if resp.status >= 400:
                raise gr.Error(resp.json()["message"])
            resp_json = await resp.json()
            choices = [
                (f"{ls['liveStreamUrl']} ({ls['description']})", ls["id"])
                for ls in resp_json
                if ls["chunk_duration"] > 0
            ]
            return gr.update(
                choices=choices,
                value=(
                    f"< {len(choices)} active stream(s). Select an active stream >"
                    if choices
                    else "< No active streams found >"
                ),
                interactive=bool(choices),
            )


async def on_url_changed(video):
    return gr.update(interactive=bool(video))


async def reconnect_live_stream(
    video_id,
    temperature,
    top_p,
    top_k,
    max_new_tokens,
    seed,
    num_frames_per_chunk,
    vlm_input_width,
    vlm_input_height,
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
    chunk_size,
    summary_duration,
    alerts_table,
    summary_prompt,
    caption_summarization_prompt,
    summary_aggregation_prompt,
    enable_chat,
    enable_cv_metadata,
    cv_pipeline_prompt,
    enable_audio,
    clear,
    enable_chat_history,
):
    if not video_id:
        yield [
            gr.update(),
        ] * 45
        return

    session: aiohttp.ClientSession = appConfig["session"]
    async with session.get(appConfig["backend"] + "/models") as resp:
        resp_json = await resp.json()
        if resp.status >= 400:
            raise gr.Error(resp_json["message"])
        model = resp_json["data"][0]["id"]

    req_json = {
        "id": video_id,
        "model": model,
        "chunk_duration": chunk_size,
        "summary_duration": summary_duration,
        "temperature": temperature,
        "seed": seed,
        "max_tokens": max_new_tokens,
        "top_p": top_p,
        "top_k": top_k,
        "stream": True,
        "enable_chat": enable_chat,
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
        "enable_cv_metadata": enable_cv_metadata,
        "enable_audio": enable_audio,
        "enable_chat_history": enable_chat_history,
    }
    if summary_prompt:
        req_json["prompt"] = summary_prompt
    if caption_summarization_prompt:
        req_json["caption_summarization_prompt"] = caption_summarization_prompt
    if summary_aggregation_prompt:
        req_json["summary_aggregation_prompt"] = summary_aggregation_prompt
    if cv_pipeline_prompt:
        req_json["cv_pipeline_prompt"] = cv_pipeline_prompt

    parsed_alerts = []
    logger.debug(f"Collected columns: {column_names[:-2]}")
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

    yield [
        gr.update(),
    ] * 44 + [video_id]

    try:
        async for response in summarize_response_async(session, req_json, video_id, enable_chat):
            yield tuple(response) + (gr.update(interactive=True),) + (
                gr.update(),
            )  # stop/delete btn and live_stream_id_preview and
    except Exception as ex:
        yield (
            gr.update(interactive=False),  # video, , , , ,
            gr.update(interactive=False),  # username, , , , ,
            gr.update(interactive=False),  # password, , , , ,
            gr.update(interactive=False),  # upload_button
            gr.update(interactive=False),  # enable_chat
            gr.update(interactive=enable_chat),  # ask_button
            gr.update(interactive=enable_chat),  # question_textbox
            video_id,  # stream_id
            "ERROR: " + str(ex),  # output_response
            "ERROR: " + str(ex),  # output_alerts
            gr.update(interactive=False),  # chunk_size
            gr.update(interactive=False),  # summary_duration
            gr.update(interactive=False),  # alerts_table
            gr.update(interactive=False),  # summary_prompt
            gr.update(interactive=False),  # caption_summarization_prompt
            gr.update(interactive=False),  # summary_aggregation_prompt
            gr.update(interactive=False),  # temperature
            gr.update(interactive=False),  # top_p
            gr.update(interactive=False),  # top_k
            gr.update(interactive=False),  # max_new_tokens
            gr.update(interactive=False),  # seed
            gr.update(interactive=False),  # num_frames_per_chunk
            gr.update(interactive=False),  # vlm_input_width
            gr.update(interactive=False),  # vlm_input_height
            gr.update(interactive=False),  # summarize_top_p
            gr.update(interactive=False),  # summarize_temperature
            gr.update(interactive=False),  # summarize_max_tokens
            gr.update(interactive=False),  # chat_top_p
            gr.update(interactive=False),  # chat_temperature
            gr.update(interactive=False),  # chat_max_tokens
            gr.update(interactive=False),  # notification_top_p
            gr.update(interactive=False),  # notification_temperature
            gr.update(interactive=False),  # notification_max_tokens
            gr.update(interactive=False),  # summarize_batch_size
            gr.update(interactive=False),  # rag_batch_size
            gr.update(interactive=False),  # rag_type
            gr.update(interactive=False),  # rag_top_k
            gr.update(interactive=False),  # active_live_streams
            gr.update(interactive=False),  # refresh_list_button
            gr.update(interactive=False),  # reconnect_button
            gr.update(interactive=False),  # enable_cv_metadata
            gr.update(interactive=False),  # cv_pipeline_prompt
            gr.update(interactive=False),  # enable_audio
            gr.update(interactive=True),  # stop/delete btn
            gr.update(""),  # live_stream_id_preview
        )


def live_stream_selected(active_live_stream):
    try:
        uuid.UUID(active_live_stream)
        return gr.update(interactive=True)
    except Exception:
        pass
    return gr.update(interactive=False)


def disable_clear():
    return gr.update(interactive=False)  # Disable the button


def build_rtsp_stream(args, app_cfg, logger_):
    global appConfig, logger, pipeline_args
    appConfig = app_cfg
    logger = logger_
    pipeline_args = args

    (
        default_prompt,
        default_caption_summarization_prompt,
        default_summary_aggregation_prompt,
        _,
    ) = get_default_prompts()

    with open(os.environ.get("CA_RAG_CONFIG", "/opt/nvidia/via/default_config.yaml")) as f:
        ca_rag_config = yaml.safe_load(f)

    stream_id = gr.State("")
    popup_visible = gr.State(False)
    all_alerts_popup_visible = gr.State(False)

    with gr.Row(equal_height=True):

        with gr.Column(scale=1):
            active_live_streams = gr.Dropdown(
                label="ACTIVE LIVE STREAMS",
                interactive=False,
                visible=True,
                elem_classes=["white-background", "bold-header"],
                allow_custom_value=True,
                value=lambda: "< Please Refresh List >",
                # choices= refresh_active_stream_list,
            )

            with gr.Row(equal_height=True):
                refresh_list_button = gr.Button(
                    value="Refresh List", interactive=True, variant="primary", size="sm", scale=0
                )
                reconnect_button = gr.Button(
                    value="Reconnect", interactive=False, variant="primary", size="sm", scale=0
                )
            gr.Markdown("##")

            live_stream_id_preview = gr.State("")
            with gr.Tabs(elem_id="video-tabs"):
                with gr.Tab("Preview"):
                    gr.Video(
                        label="PREVIEW",
                        interactive=False,
                        visible=True,
                        container=True,
                        elem_classes="white-background",
                        streaming=True,
                        autoplay=True,
                        value=get_live_stream_preview_chunks,
                        inputs=[live_stream_id_preview],
                        show_download_button=False,
                    )
                with gr.Tab("Set-of-Marks Preview", interactive=False) as set_marks_tab:
                    set_marks_video = gr.Video(
                        label="Set-of-Marks Preview",
                        interactive=False,
                        visible=True,
                        container=True,
                        elem_classes="white-background",
                        streaming=True,
                        autoplay=True,
                        value=get_overlay_live_stream_preview_chunks,
                        inputs=[live_stream_id_preview],
                        show_download_button=False,
                    )
            video = gr.Textbox(
                label="STREAM",
                placeholder="rtsp://",
                interactive=True,
                visible=True,
                container=True,
                elem_classes="white-background",
            )
            with gr.Accordion(
                label="RTSP Credentials",
                elem_classes="white-background",
                open=False,
            ):
                username = gr.Textbox(
                    label="RTSP Username",
                    placeholder="(OPTIONAL)",
                    interactive=True,
                    visible=True,
                    container=True,
                    elem_classes="white-background",
                )
                password = gr.Textbox(
                    label="RTSP Password",
                    placeholder="(OPTIONAL)",
                    interactive=True,
                    visible=True,
                    container=True,
                    elem_classes="white-background",
                )

            with gr.Tabs(elem_id="sub-tabs"):
                with gr.Tab("Prompt"):
                    with gr.Row():
                        chunk_size = gr.Textbox(
                            label="CHUNK SIZE (in seconds)",
                            value=10,
                            interactive=True,
                            visible=True,
                            elem_classes=["white-background", "bold-header"],
                        )
                        summary_duration = gr.Dropdown(
                            choices=SUMMARY_DURATION,
                            label="SUMMARY DURATION",
                            value=60,
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

                    with gr.Accordion(
                        label="STREAM/FILE SETTINGS",
                        elem_classes=["white-background", "bold-header"],
                        open=True,
                    ):
                        enable_chat = gr.Checkbox(value=False, label="Enable chat for the stream")

                        enable_chat_history = gr.Checkbox(value=True, label="Enable chat history")

                        enable_audio = gr.Checkbox(
                            value=False,
                            label="Enable Audio",
                            # elem_classes=["white-background", "bold-header"],
                            visible=bool(os.environ.get("ENABLE_AUDIO", "false").lower() == "true"),
                        )

                        enable_cv_metadata = gr.Checkbox(
                            value=False,
                            label="Enable CV Metadata",
                            # elem_classes=["white-background", "bold-header"],
                            visible=bool(
                                os.environ.get("DISABLE_CV_PIPELINE", "true").lower() == "false"
                            ),
                        )
                        cv_pipeline_prompt = gr.TextArea(
                            label="CV PIPELINE PROMPT (OPTIONAL)",
                            # elem_classes=["white-background", "bold-header"],
                            lines=1,
                            max_lines=1,
                            value="person . forklift . robot . fire . spill ",
                            visible=bool(
                                os.environ.get("DISABLE_CV_PIPELINE", "true").lower() == "false"
                            ),
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

            with gr.Row(equal_height=True):
                upload_button = gr.Button(
                    value="Start streaming & summarization",
                    interactive=False,
                    variant="primary",
                    size="sm",
                    scale=1,
                )

        def check_positive_whole_number(attr: str, attr_name: str):
            """
            Check if the attribute is a valid positive whole number
            """
            if "." in attr:
                raise gr.Error(f"{attr_name} must be a whole number")
            try:
                if int(attr) <= 0:
                    raise gr.Error(f"{attr_name} must be greater than 0")
            except ValueError:
                raise gr.Error(f"{attr_name} must be a valid number")
            return True

        chunk_size.change(
            check_positive_whole_number, inputs=[chunk_size, gr.State("Chunk Size")], outputs=[]
        )

        with gr.Column(scale=3):
            with gr.Column():
                with gr.Row(equal_height=True, elem_classes="align-right-row"):
                    all_alerts_button = gr.Button(
                        value="All Alerts",
                        variant="primary",
                        size="sm",
                        scale=0,
                        elem_classes=["small-button"],
                    )
                    clear = gr.Button(
                        "Stop/Delete Stream",
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
                    with gr.Tab("SUMMARIES"):
                        output_response = gr.TextArea(
                            interactive=False, max_lines=30, lines=30, show_label=False
                        )
                    with gr.Tab("ALERTS"):
                        output_alerts = gr.TextArea(
                            interactive=False, max_lines=30, lines=30, show_label=False
                        )
                    with gr.Tab("CHAT"):
                        chatbot = gr.Chatbot(
                            [],
                            label="RESPONSE",
                            bubble_full_width=False,
                            avatar_images=(USER_AVATAR_ICON.name, CHATBOT_AVATAR_ICON.name),
                            height=550,
                            elem_classes="white-background",
                        )
                        with gr.Row(equal_height=True, variant="default"):
                            question_textbox = gr.Textbox(
                                label="Ask a question",
                                interactive=False,
                                scale=3,  # This makes it take up 3 parts of the available space
                            )
                            with gr.Column(
                                scale=1
                            ):  # This column takes up 1 part of the available space
                                ask_button = gr.Button("Ask", interactive=False)
                                reset_chat_button = gr.Button("Reset Chat", interactive=False)

    # Add popup container
    with gr.Column(visible=False, scale=3, elem_classes="modal-container") as popup_container:
        # Add the close button inside the popup
        with gr.Row(equal_height=True, elem_classes="align-right-row"):
            gr.Markdown("**Parameters**")
            close_popup_button = gr.Button(
                "✖",  # Cross sign
                variant="secondary",
                size="sm",
                elem_classes="close-button",  # Add custom class for styling
            )

        with gr.Row(equal_height=True):
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
    video.change(on_url_changed, inputs=[video], outputs=[upload_button])
    video.change(on_url_changed, inputs=[video], outputs=[add_alert_btn])

    # Add event handler for rag_type changes
    def update_chat_history_checkbox(selected_rag_type):
        is_graph_rag = selected_rag_type == "graph-rag"
        return gr.update(value=is_graph_rag, interactive=is_graph_rag)

    rag_type.change(
        fn=update_chat_history_checkbox, inputs=[rag_type], outputs=[enable_chat_history]
    )

    # Retreive stream settings from cache file
    # and update the UI with the stream settings
    cache_stream_settings = RetrieveCache(logger=logger)
    active_live_streams.input(
        cache_stream_settings.retreive_UI_updates,
        inputs=[active_live_streams],
        outputs=[
            enable_chat,
            chunk_size,
            summary_duration,
            summary_prompt,
            caption_summarization_prompt,
            summary_aggregation_prompt,
            temperature,
            top_p,
            top_k,
            max_new_tokens,
            seed,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
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
            enable_cv_metadata,
            cv_pipeline_prompt,
            enable_audio,
            alerts_table,
            table_state,
        ],
    )

    def summary_duration_n_chunk_size(summary_duration, chunk_size):
        if int(summary_duration) < int(chunk_size):
            raise gr.Error("Summary duration must be greater than chunk size")
        if int(summary_duration) % int(chunk_size) != 0:
            raise gr.Error("Summary duration must be a multiple of chunk size")
        return True

    upload_button.click(
        lambda enable_chat, request: (
            gr.update(interactive=bool(enable_chat)),
            gr.update(interactive=bool(enable_chat)),
        ),
        inputs=[ask_button, question_textbox],
        outputs=[ask_button, question_textbox],
    ).success(
        summary_duration_n_chunk_size,
        inputs=[summary_duration, chunk_size],
        outputs=[],
    ).then(
        add_rtsp_stream,
        inputs=[
            video,
            username,
            password,
            temperature,
            top_p,
            top_k,
            max_new_tokens,
            seed,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
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
            chunk_size,
            summary_duration,
            alerts_table,
            summary_prompt,
            caption_summarization_prompt,
            summary_aggregation_prompt,
            enable_chat,
            enable_cv_metadata,
            cv_pipeline_prompt,
            enable_audio,
            clear,
            enable_chat_history,
        ],
        outputs=[
            video,
            username,
            password,
            upload_button,
            enable_chat,
            ask_button,
            question_textbox,
            stream_id,
            output_response,
            output_alerts,
            chunk_size,
            summary_duration,
            alerts_table,
            summary_prompt,
            caption_summarization_prompt,
            summary_aggregation_prompt,
            temperature,
            top_p,
            top_k,
            max_new_tokens,
            seed,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
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
            active_live_streams,
            refresh_list_button,
            reconnect_button,
            enable_cv_metadata,
            cv_pipeline_prompt,
            enable_audio,
            live_stream_id_preview,
            clear,
        ],
    )

    refresh_list_button.click(fn=refresh_active_stream_list, outputs=[active_live_streams])
    active_live_streams.change(
        live_stream_selected, inputs=[active_live_streams], outputs=[reconnect_button]
    )
    reconnect_button.click(
        lambda enable_chat, request: (
            gr.update(interactive=bool(enable_chat)),
            gr.update(interactive=bool(enable_chat)),
        ),
        inputs=[ask_button, question_textbox],
        outputs=[ask_button, question_textbox],
    ).then(
        fn=reconnect_live_stream,
        inputs=[
            active_live_streams,
            temperature,
            top_p,
            top_k,
            max_new_tokens,
            seed,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
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
            chunk_size,
            summary_duration,
            alerts_table,
            summary_prompt,
            caption_summarization_prompt,
            summary_aggregation_prompt,
            enable_chat,
            enable_cv_metadata,
            cv_pipeline_prompt,
            enable_audio,
            clear,
            enable_chat_history,
        ],
        outputs=[
            video,
            username,
            password,
            upload_button,
            enable_chat,
            ask_button,
            question_textbox,
            stream_id,
            output_response,
            output_alerts,
            chunk_size,
            summary_duration,
            alerts_table,
            summary_prompt,
            caption_summarization_prompt,
            summary_aggregation_prompt,
            temperature,
            top_p,
            top_k,
            max_new_tokens,
            seed,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
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
            active_live_streams,
            refresh_list_button,
            reconnect_button,
            enable_cv_metadata,
            cv_pipeline_prompt,
            enable_audio,
            clear,
            live_stream_id_preview,
        ],
    )
    # Add All Alerts popup container
    with gr.Column(visible=False, scale=3, elem_classes="modal-container") as all_alerts_popup:
        with gr.Row(equal_height=True, elem_classes="align-right-row"):
            gr.Markdown("**All Alerts**")
            close_all_alerts_button = gr.Button(
                "✖",
                variant="secondary",
                size="sm",
                elem_classes="close-button",
            )

        all_alerts_headers = [
            "Alert Name",
            "Events",
            "Live Stream ID",
            "Timestamp",
            "Alert Text",
        ]
        all_alerts = gr.Dataframe(
            headers=all_alerts_headers,
            interactive=False,
        )

    # Function to toggle the All Alerts popup
    def toggle_all_alerts_popup(visible):
        return gr.update(visible=not visible), not visible

    # Function to update and show all alerts
    async def show_all_alerts_popup(visible, click):
        if not (click or visible):  # Only update if popup is already visible/"All Alerts" clicked
            return gr.update(), visible, gr.update()
        try:
            session: aiohttp.ClientSession = appConfig["session"]
            async with session.get(appConfig["backend"] + "/alerts/recent") as resp:
                if resp.status >= 400:
                    resp_json = await resp.json()
                    logger.error(f"Error: {resp_json}")
                    return (
                        gr.update(visible=True),
                        True,
                        gr.update(value=[["" for _ in range(5)]] * 15, headers=all_alerts_headers),
                    )

                alerts_list = await resp.json()
                rows = []
                for alert in alerts_list:
                    row = [
                        alert.get("alert_name", "N/A"),
                        ", ".join(alert.get("detected_events", [])) or "N/A",
                        alert.get("live_stream_id", "N/A"),
                        alert.get("ntp_timestamp", "N/A"),
                        alert.get("alert_text", "N/A"),
                    ]
                    rows.append(row)

                if len(rows) < 15:
                    empty_row = [["" for _ in range(5)]] * (15 - len(rows))
                    rows.extend(empty_row)

                visible_updated = True if click else visible

                return (
                    gr.update(visible=visible_updated),
                    visible_updated,
                    gr.update(value=rows, headers=all_alerts_headers),
                )

        except Exception as e:
            logger.error(f"Error fetching alerts: {e}")
            return (
                gr.update(visible=visible_updated),
                visible_updated,
                gr.update(value=[["" for _ in range(5)]] * 15, headers=all_alerts_headers),
            )

    # Connect the buttons to toggle the popups
    all_alerts_button.click(
        show_all_alerts_popup,
        inputs=[all_alerts_popup_visible, gr.State(True)],
        outputs=[all_alerts_popup, all_alerts_popup_visible, all_alerts],
    )

    close_all_alerts_button.click(
        toggle_all_alerts_popup,
        inputs=[all_alerts_popup_visible],
        outputs=[all_alerts_popup, all_alerts_popup_visible],
    )

    output_alerts.change(
        show_all_alerts_popup,
        inputs=[all_alerts_popup_visible, gr.State(False)],
        outputs=[all_alerts_popup, all_alerts_popup_visible, all_alerts],
    )

    ask_button.click(
        ask_question,
        inputs=[
            question_textbox,
            ask_button,
            reset_chat_button,
            video if video is not None else active_live_streams,
            chatbot,
            stream_id,
            chunk_size,
            temperature,
            seed,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
            max_new_tokens,
            top_p,
            top_k,
        ],
        outputs=[chatbot, ask_button, reset_chat_button, question_textbox],
    )

    def update_set_marks_interactivity(enable_cv_metadata):
        return gr.update(interactive=enable_cv_metadata)

    enable_cv_metadata.change(
        update_set_marks_interactivity, inputs=[enable_cv_metadata], outputs=[set_marks_tab]
    )

    reset_chat_button.click(
        ask_question,
        inputs=[
            gr.State("/clear"),
            ask_button,
            reset_chat_button,
            video if video is not None else active_live_streams,
            chatbot,
            stream_id,
            chunk_size,
            temperature,
            seed,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
            max_new_tokens,
            top_p,
            top_k,
        ],
        outputs=[chatbot, ask_button, reset_chat_button, question_textbox],
    ).then(
        reset_chat,
        inputs=[chatbot],
        outputs=[chatbot],
    )

    video.change(on_url_changed, inputs=[video], outputs=[upload_button])

    clear.click(
        gradio_reset,
        [stream_id],
        [
            video,
            username,
            password,
            upload_button,
            enable_chat,
            ask_button,
            reset_chat_button,
            question_textbox,
            stream_id,
            output_response,
            output_alerts,
            chatbot,
            chunk_size,
            summary_duration,
            alerts_table,
            summary_prompt,
            caption_summarization_prompt,
            summary_aggregation_prompt,
            temperature,
            top_p,
            top_k,
            max_new_tokens,
            seed,
            num_frames_per_chunk,
            vlm_input_width,
            vlm_input_height,
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
            active_live_streams,
            refresh_list_button,
            reconnect_button,
            enable_cv_metadata,
            cv_pipeline_prompt,
            enable_audio,
            live_stream_id_preview,
            set_marks_video,
            set_marks_tab,
        ],
        queue=None,
    ).then(
        disable_clear,  # Run after reset
        [],
        [clear],  # Disable clear button
    )
