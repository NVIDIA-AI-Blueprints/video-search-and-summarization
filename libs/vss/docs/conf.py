# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Sphinx configuration for the VSS Python API documentation."""

from importlib.metadata import version

project = "VSS Python Libraries"
copyright = "2026, NVIDIA Corporation & Affiliates"
author = "NVIDIA Corporation & Affiliates"
release = version("vss")
version = release

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
]

autodoc_class_signature = "separated"
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_typehints_format = "short"

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

html_theme = "nvidia_sphinx_theme"
html_title = f"{project} {release}"
html_show_sourcelink = False

exclude_patterns = ["_build", "video_processing_validation.rst"]
