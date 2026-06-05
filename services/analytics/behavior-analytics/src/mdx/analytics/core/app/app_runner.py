# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import argparse
import logging
import os
import signal
import uuid

from datetime import datetime
from functools import partial

from mdx.analytics.core.app.app_base import BaseApp
from mdx.analytics.core.app.scheduler.app_scheduler import Task
from mdx.analytics.core.app.scheduler.app_scheduler_mp import MultiprocessingScheduler
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import Notification
from mdx.analytics.core.stream.sink.sink_factory import get_sink
from mdx.analytics.core.stream.source.source_base import Source
from mdx.analytics.core.stream.source.source_factory import get_source
from mdx.analytics.core.transform.calibration.calibration_listener import (
    CalibrationListener,
    deserialize_calibration_message,
)
from mdx.analytics.core.transform.config.config_applier import ConfigApplier
from mdx.analytics.core.transform.config.config_listener import (
    ConfigListener,
    deserialize_config_message,
)
from mdx.analytics.core.transform.config.config_publisher import ConfigPublisher
from mdx.analytics.core.utils.io_utils import ValidateFile, load_json_from_file, validate_file_path
from mdx.analytics.core.logger_setup import setup_logging

import json
from pydantic import ValidationError


logger = logging.getLogger(__name__)


def run(app_cls: type[BaseApp]) -> None:
    """
    Entry point function that parses command line arguments and starts the application runner.
    
    :param type[BaseApp] app_cls: The application class that extends BaseApp to be executed
    :return: None
    :raises SystemExit: If argument parsing fails or required arguments are missing
    :raises Exception: If AppRunner initialization or startup fails
    """

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--config",
        type=validate_file_path, required=True,
        action=ValidateFile,
        help="The input app config file"
    )

    parser.add_argument(
        "--calibration",
        type=validate_file_path,
        action=ValidateFile,
        required=False,
        default=None,
        help="The input calibration file (optional)"
    )

    parser.add_argument(
        "--log",
        type=validate_file_path,
        default="configs/logging_config.json",
        help="The input logging config file"
    )

    args = parser.parse_args()

    # Resolve to absolute so spawn-mode workers (which inherit CWD but may run
    # from a different effective root) can re-apply logging consistently.
    args.log = os.path.abspath(args.log)

    setup_logging(args.log)
    logger.info(f"App config file path: {args.config}")
    if args.calibration:
        logger.info(f"Calibration file path: {args.calibration}")
    else:
        logger.info("No calibration file provided")
    logger.info(f"Logging config file path: {args.log}")

    AppRunner(app_cls, args.config, args.calibration, args.log).start()


class AppRunner:
    """Application runner that manages the lifecycle of analytics applications.
    
    Handles configuration loading, calibration setup, task scheduling, and
    graceful shutdown of analytics applications.

    :ivar type[BaseApp] _app_cls: The application class that extends BaseApp to be executed
    :ivar str | None _calibration_path: Path to the calibration configuration file
    :ivar str | None _log_config_path: Path to the logging configuration file
    :ivar AppConfig _config: Loaded application configuration
    :ivar CalibrationListener _calibration_listener: Listener for calibration updates
    :ivar list[Task] _tasks: List of tasks to be executed
    :ivar MultiprocessingScheduler _scheduler: Multiprocessing scheduler for task execution
    """

    def __init__(self, app_cls: type[BaseApp], config_path: str, calibration_path: str | None, log_config_path: str | None = None) -> None:
        """Initialize the AppRunner with application class, configuration, and calibration settings.

        :param type[BaseApp] app_cls: The application class that extends BaseApp to be executed
        :param str config_path: Path to the application configuration file
        :param str | None calibration_path: Path to the calibration configuration file (optional)
        :param str | None log_config_path: Path to the logging configuration file (optional). Forwarded
            to each Task so spawn-mode workers can re-apply logging in their fresh interpreters.
        :return: None
        :raises Exception: If config loading or calibration listener creation fails
        """

        self._app_cls = app_cls
        self._calibration_path = calibration_path
        self._log_config_path = log_config_path
        self._config = self._load_config(config_path)
        logger.info(f"App config (defaults excluded):\n{self._config.model_dump_json(indent=2, exclude_defaults=True)}")
        # Per-process UUID appended to the calibration consumer group so every
        # replica receives every calibration notification (without it, replicas
        # sharing kafka.group land in the same group and only one would consume
        # each message). Same logic for the dynamic-config consumer group.
        self._calibration_replica_tag = uuid.uuid4().hex
        self._config_listener_replica_tag = uuid.uuid4().hex
        # Stable bootstrap ref-id for this main process; web-api echoes it on
        # the upsert-all reply so we filter out other deployments' replies.
        self._config_bootstrap_ref_id = f"behavior-analytics-{uuid.uuid4().hex}"
        self._calibration_listener = self._load_calibration_listener()
        self._config_listener = self._load_config_listener()
        self._tasks: list[Task] = []
        self._scheduler = MultiprocessingScheduler()


    def start(self) -> None:
        """
        Start the application by creating processors, setting up signal handlers, and running tasks.
        
        :return: None
        :raises RuntimeError: If no processors are registered in the application
        :raises Exception: If app initialization, processor setup, or scheduler submission fails
        """

        try:
            app = self._app_cls(self._config, self._calibration_path)

            for processor in app.get_processors():

                for i in range(0, processor.num_workers):
                    # Create task with picklable data only
                    self._tasks.append(Task(
                        app_name = self._app_cls.__name__,
                        id = i,
                        poller_name = processor.poller.__name__,
                        handler_name = processor.handler.__name__,
                        shutdown_hook_name = app.close.__name__,
                        app_class = self._app_cls,
                        config = self._config,
                        calibration_path = self._calibration_path,
                        log_config_path = self._log_config_path
                    ))

            if not self._tasks:
                raise RuntimeError(f"FATAL - No processors registered in app {self._app_cls}, refer BaseApp.register_processor(...)")

            self._calibration_listener.start()
            self._config_listener.start()

            signal.signal(signal.SIGINT, self._sig_shutdown)
            signal.signal(signal.SIGTERM, self._sig_shutdown)

            self._scheduler.submit(self._tasks)

        except Exception as e:
            logger.error(f"FATAL - Error in app: {str(e)}", exc_info=True)
            self.close()


    def close(self) -> None:
        """Close the calibration / config listeners and shut down the scheduler.

        :return: None
        """

        if self._calibration_listener:
            self._calibration_listener.close()

        if self._config_listener:
            self._config_listener.close()

        self._scheduler.shutdown()


    def _sig_shutdown(self, signum, _) -> None:
        """
        Signal handler for shutdown signals (SIGINT, SIGTERM) that gracefully closes the application.
        
        :param int signum: The signal number received
        :param _: Frame object (unused)
        :return: None
        """
        
        logger.info(f"Exiting app, shutdown signal {signal.Signals(signum).name} received.")
        self.close()


    def _load_config(self, config_path: str) -> AppConfig:
        """
        Load and validate the application configuration from the specified file path.
        
        :param str config_path: Path to the configuration file
        :return AppConfig: The loaded application configuration
        :raises SystemExit: If the config file cannot be loaded or contains invalid data:
            - Invalid JSON syntax (shows line/column of error)
            - Missing required fields or invalid types (shows field-specific errors)
            - File not found or any other loading errors
        """

        try:
            config_data = load_json_from_file(config_path)
            
            return AppConfig(**config_data)
            
        except json.JSONDecodeError as e:
            logger.error(f"FATAL - Config file `{config_path}` contains invalid JSON: {e}")
            exit(1)
            
        except ValidationError as e:
            logger.error(f"FATAL - Config file `{config_path}` has invalid structure: {e}")
            exit(1)
            
        except Exception as e:
            logger.error(f"FATAL - Unexpected error loading config from `{config_path}`: {e}")
            exit(1)

    def _load_calibration_listener(self) -> CalibrationListener:
        """
        Create the main-process calibration listener.

        Wires a ``source.poll`` closure that consumes ``mdx-notification``
        with the calibration-specific group-id suffix and the module-level
        :func:`deserialize_calibration_message` (which filters by Kafka
        key ``"calibration"`` and parses the ``event.type`` / ``timestamp``
        headers). Mirrors :meth:`_load_config_listener` in shape.

        :return CalibrationListener: Configured listener instance (call
            ``start()`` to begin consuming).
        """

        def _read_notifications(source: Source) -> list[Notification]:
            return source.poll(
                src_key="notification",
                msg_deserializer=deserialize_calibration_message,
                group_id_suffix=self._calibration_replica_tag,
            )

        source = get_source(self._config)
        return CalibrationListener(self._config, partial(_read_notifications, source))

    def _load_config_listener(self) -> ConfigListener:
        """
        Create the main-process dynamic-config listener.

        Wires together a fresh ``ConfigApplier`` (against main's local
        AppConfig copy), a ``ConfigPublisher`` for emitting ``request-config``
        and ``ack``, and a ``source.poll`` closure that consumes
        ``mdx-notification`` with the per-process replica tag.

        Workers do **not** consume notifications directly any more; they
        watch :data:`CONFIG_DIR` for files written by this listener.

        :return ConfigListener: Configured listener instance (call
            ``start()`` to begin consuming).
        """

        # The deserializer needs the active source type to decide whether
        # to accept a direct-publisher `upsert` (reference-id == "kafka" /
        # "redis" / "mqtt", with the broker timestamp appended for
        # uniqueness). Looked up once at listener construction; sourceType
        # is not hot-reloadable so this is stable for the process lifetime.
        source_type = self._config.get_app_config("sourceType")

        def _read_config_messages(source: Source) -> list:
            return source.poll(
                src_key="notification",
                msg_deserializer=partial(deserialize_config_message, source_type=source_type),
                group_id_suffix=self._config_listener_replica_tag,
            )

        source = get_source(self._config)
        sink = get_sink(self._config)
        applier = ConfigApplier(self._config)
        publisher = ConfigPublisher(sink)
        return ConfigListener(
            config=self._config,
            applier=applier,
            publisher=publisher,
            notification_consumer_fn=partial(_read_config_messages, source),
            bootstrap_ref_id=self._config_bootstrap_ref_id,
        )
