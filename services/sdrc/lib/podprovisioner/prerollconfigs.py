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

import logging
import json

logger = logging.getLogger(__name__)

class prerollconfigs:
    def __init__(self, config) -> None:
        self.config = config
        self.pr = config["WDM_PREROLL_CONFIG_FILE"]
        self.prc = self.setConfigFileContent ()
        return
    
    def setConfigFileContent (self):
        o = {}
        try:
            with open(self.pr, "r") as f:
                d = f.read ()
                o = json.loads (d)
        except json.decoder.JSONDecodeError as j:
            logger.warning (f"file {self.pr}: {j}")
        except Exception as e:
            logger.warning (f"file not loaded: {e}")
        return None if len (o) == 0 else o

