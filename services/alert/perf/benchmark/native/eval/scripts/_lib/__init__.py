"""Private shared library modules for eval scripts.

Import from the concrete submodules (``_common``, ``_config``, ...) directly.
This package intentionally re-exports nothing: importing ``_config`` pulls in
``yaml`` and reads ``eval/ngc-resources.yml``, which breaks lightweight
consumers (e.g. ``cache_trt_engines`` in minimal after_script shells that
only have the stdlib available).
"""
