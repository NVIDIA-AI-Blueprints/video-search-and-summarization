#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2022-2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

set -e
SCRIPT_DIR=$(dirname ${BASH_SOURCE})

# Install required pip packages for documentation build
install_pip_deps() {
    local PYTHON_SH="$SCRIPT_DIR/tools/packman/python.sh"

    # Check if swagger-plugin-for-sphinx is installed
    if ! "$PYTHON_SH" -c "import swagger_plugin_for_sphinx" 2>/dev/null; then
        echo "Installing swagger-plugin-for-sphinx..."
        "$PYTHON_SH" -m pip install swagger-plugin-for-sphinx==6.1.0 --quiet
    fi
}

build_docs() {
    "$SCRIPT_DIR/repo.sh" docs --warn-as-error=1 "$@"
}

copy_root_index() {
    cp -v "$SCRIPT_DIR/resources/root-index.html" "$SCRIPT_DIR/_build/docs/index.html"
}

main() {
    install_pip_deps
    build_docs "$@"
    copy_root_index
}

main "$@"
