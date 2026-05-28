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

import logging
import logging.config
import json
import os
import time
from copy import deepcopy

logger = logging.getLogger(__name__)


class MicrosecondFormatter(logging.Formatter):
    """Custom formatter that supports microseconds in timestamps"""
    
    def formatTime(self, record, datefmt=None) -> str:
        ct = self.converter(record.created)
        if datefmt:
            s = time.strftime(datefmt, ct)
        else:
            s = time.strftime('%Y-%m-%d %H:%M:%S', ct)
        # Add microseconds
        s = f"{s}.{int(record.created % 1 * 1_000_000):06d}"
        return s


def setup_logging(config_path: str | None = None) -> None:
    def _setup_default_logging():
        default_config = {
            'version': 1,
            'disable_existing_loggers': False,

            'formatters': {
                'standard': {
                    "()": MicrosecondFormatter,
                    "format": "[%(asctime)s] [%(levelname)s] [%(processName)s] [%(filename)s: %(lineno)d] - %(message)s"
                },
            },
            'handlers': {
                'console': {
                    'class': 'logging.StreamHandler',
                    'formatter': 'standard',
                    'level': 'DEBUG',
                    'stream': 'ext://sys.stdout',
                },
            },
            "loggers": {
                "mdx.analytics.core.stream.state.behavior": {
                    "level": "WARNING",
                    "handlers": ["console"],
                    "propagate": False
                },
            },
            'root': {
                'level': 'INFO',
                'handlers': ['console'],
            },
        }
        logging.config.dictConfig(default_config)
        logger.warning(f"Logging config not found at {config_path}, using default config.")
        
        # Create a JSON-serializable version for logging
        loggable_config = deepcopy(default_config)
        del loggable_config['formatters']['standard']['()']
        loggable_config['formatters']['standard']['use_microseconds'] = True
        logger.info(f"Default logging config:\n{json.dumps(loggable_config, indent=2)}")

    if config_path and os.path.exists(config_path):
        with open(config_path) as f:
            config = json.load(f)
        
        loggable_config = deepcopy(config)
        # Inject custom formatter class if needed
        if "formatters" in config:
            for _, formatter_config in config["formatters"].items():
                if formatter_config.get("use_microseconds"):
                    formatter_config["()"] = MicrosecondFormatter
                    formatter_config.pop("use_microseconds")
        
        logging.config.dictConfig(config)
        logger.info(f"Logging config:\n{json.dumps(loggable_config, indent=2)}")
    else:
        _setup_default_logging()
