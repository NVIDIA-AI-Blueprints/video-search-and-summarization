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

"""NVIDIA Vision AI harness library.

Public entry points:

* :mod:`nvidia_vision_ai.vlm` -- ask a VLM about a video (``analyze``) and list its models.
* :mod:`nvidia_vision_ai.memory` -- the ``JobStore`` protocol and job records.
* :mod:`nvidia_vision_ai.tool` -- the plugin contract: the ``Tool`` base class every
  ``vision-ai <tool>`` command implements, so a plugin depends only on this library.
* :mod:`nvidia_vision_ai.knowledge` -- retrieval adapters behind one ``BackendAdapter``
  interface (``await get_retriever(backend, config)``; ``arango_graph`` also needs an
  LLM, so it is built directly); each backend's SDK ships as an extra.

``vlm`` and ``memory`` take a pydantic model and return one.

The library reads no config files and has no default paths; the CLI owns both.
"""

from ._foundation import BackendResponseError
from ._foundation import BackendUnreachableError
from ._foundation import ConfigurationError
from ._foundation import InvalidRequestError
from ._foundation import LibraryError
from .tool import Tool

__all__ = [
    "BackendResponseError",
    "BackendUnreachableError",
    "ConfigurationError",
    "InvalidRequestError",
    "LibraryError",
    "Tool",
]
