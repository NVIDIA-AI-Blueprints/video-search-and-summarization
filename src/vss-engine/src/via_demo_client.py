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

import argparse
import asyncio
import logging
import logging.handlers
import os
import sys
import tempfile

import aiohttp
import gradio as gr
import pkg_resources
import uvicorn
from fastapi import FastAPI

from client.rtsp_stream import build_rtsp_stream
from client.summarization import build_summarization

LOGDIR = "/tmp/via-logs/"
logger = None
handler = None

pipeline_args = None
enable_logs = True
template_queries = {}
appConfig = {}
app = FastAPI()

with tempfile.NamedTemporaryFile() as temp:
    temp.write(pkg_resources.resource_string(__name__, "client/assets/kaizen-theme.json"))
    temp.flush()
    kui_theme = gr.themes.Default().load(temp.name)
kui_styles = pkg_resources.resource_string(__name__, "client/assets/kaizen-theme.css").decode(
    "utf-8"
)

APP_BAR = pkg_resources.resource_string(__name__, "client/assets/app_bar.html").decode("utf-8")


def build_logger(logger_name, logger_filename):
    global handler

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Set the format of root handlers
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO)
    logging.getLogger().handlers[0].setFormatter(formatter)

    # Capture Python warnings
    logging.captureWarnings(True)

    # Redirect stdout and stderr to loggers
    stdout_logger = logging.getLogger("stdout")
    stdout_logger.setLevel(logging.INFO)
    sl = StreamToLogger(stdout_logger, logging.INFO)
    sys.stdout = sl

    stderr_logger = logging.getLogger("stderr")
    stderr_logger.setLevel(logging.ERROR)
    sl = StreamToLogger(stderr_logger, logging.ERROR)
    sys.stderr = sl

    # Get logger
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)

    # Add a file handler for all loggers
    if handler is None:
        os.makedirs(LOGDIR, exist_ok=True)
        filename = os.path.join(LOGDIR, logger_filename)
        handler = logging.handlers.TimedRotatingFileHandler(filename, when="D", utc=True)
        handler.setFormatter(formatter)

        for name, item in logging.root.manager.loggerDict.items():
            if isinstance(item, logging.Logger):
                item.addHandler(handler)

        # --- FINE-GRAINED UVICORN CONFIGURATION ---
        # Get the specific loggers Uvicorn uses
        uvicorn_error_logger = logging.getLogger("uvicorn.error")
        uvicorn_access_logger = logging.getLogger("uvicorn.access")

        # Add your file handler to them
        uvicorn_error_logger.addHandler(handler)
        uvicorn_access_logger.addHandler(handler)

        # Disable propagation to prevent duplicate logs in the console
        # Uvicorn's default handlers will still print to console,
        # and our root logger's console_handler would do it again without this.
        uvicorn_error_logger.propagate = False
        uvicorn_access_logger.propagate = False

    return logger


class StreamToLogger(object):
    """
    Fake file-like stream object that redirects writes to a logger instance.
    """

    def __init__(self, logger, log_level=logging.INFO):
        self.terminal = sys.stdout
        self.logger = logger
        self.log_level = log_level
        self.linebuf = ""

    def __getattr__(self, attr):
        return getattr(self.terminal, attr)

    def write(self, buf):
        temp_linebuf = self.linebuf + buf
        self.linebuf = ""
        for line in temp_linebuf.splitlines(True):
            # From the io.TextIOWrapper docs:
            #   On output, if newline is None, any '\n' characters written
            #   are translated to the system default line separator.
            # By default sys.stdout.write() expects '\n' newlines and then
            # translates them so this is still cross platform.
            if line[-1] == "\n":
                self.logger.log(self.log_level, line.rstrip())
            else:
                self.linebuf += line

    def flush(self):
        if self.linebuf != "":
            self.logger.log(self.log_level, self.linebuf.rstrip())
        self.linebuf = ""


def build_demo(args):
    # need it here for example

    with gr.Blocks(
        title="Video Search and Summarization Agent",
        theme=kui_theme,
        css=kui_styles,
        analytics_enabled=False,
    ) as demo:
        gr.HTML(APP_BAR)

        with gr.Tabs(elem_id="via-tabs"):
            with gr.Tab("VIDEO FILE SUMMARIZATION & Q&A"):
                args.image_mode = False
                build_summarization(args, appConfig, logger)
            with gr.Tab("LIVE STREAM SUMMARIZATION"):
                build_rtsp_stream(args, appConfig, logger)
            with gr.Tab("IMAGE FILE SUMMARIZATION & Q&A"):
                args.image_mode = True
                build_summarization(args, appConfig, logger)

    return demo


async def main():
    timeout = aiohttp.ClientTimeout(total=0)
    appConfig["session"] = aiohttp.ClientSession(
        timeout=timeout, connector=aiohttp.TCPConnector(limit=16)
    )

    config = uvicorn.Config(app, host=pipeline_args.host, port=int(pipeline_args.port), workers=5)
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument("--host", type=str, help="Address to run server on", default="0.0.0.0")
    parser.add_argument("--port", type=str, help="port to run server on", default="7860")
    parser.add_argument(
        "--backend",
        type=str,
        help="Backend server address and port",
        default="http://127.0.0.1:8000/",
    )
    parser.add_argument(
        "--examples-streams-directory",
        type=str,
        default="streams",
        help="Directory where streams are stored for the gradion examples",
    )

    args = parser.parse_args()
    pipeline_args = args
    logger = build_logger("gradio_web_server", "gradio_web_server.log")

    appConfig["backend"] = args.backend

    demo = build_demo(args)
    demo.queue(status_update_rate=10, api_open=False, max_size=8, default_concurrency_limit=5)
    demo.show_api = False
    app = gr.mount_gradio_app(app, demo, path="/")

    asyncio.run(main())
