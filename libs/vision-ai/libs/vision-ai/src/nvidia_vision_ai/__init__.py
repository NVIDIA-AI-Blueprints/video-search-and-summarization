# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""NVIDIA Vision AI harness library.

Public entry points:

* :mod:`nvidia_vision_ai.vlm` -- ask a VLM about a video (``analyze``) and list its models.
* :mod:`nvidia_vision_ai.memory` -- a ``JobStore`` protocol and a SQLite implementation.
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

__all__ = [
    "BackendResponseError",
    "BackendUnreachableError",
    "ConfigurationError",
    "InvalidRequestError",
    "LibraryError",
]
