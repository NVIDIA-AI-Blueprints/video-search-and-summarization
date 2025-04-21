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
