# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""VIA Exception"""

from via_logger import logger


class ViaException(Exception):

    def __init__(
        self, message: str, code="InternalServerError", status_code=500, *args: object
    ) -> None:
        """VIA Exception constructor

        Args:
            message (str): Detailed error message
            code (str, optional): A short code for the error. Defaults to "InternalServerError".
            status_code (int, optional): HTTP error code. Defaults to 500.
        """
        super().__init__(code, message, *args)
        self._status_code = status_code
        self._code = code
        self._message = message
        logger.error(message)

    @property
    def status_code(self):
        return self._status_code

    @property
    def code(self):
        return self._code

    @property
    def message(self):
        return self._message

    def __str__(self) -> str:
        return f"ViaException - code: {self._code} message: {self._message}"
