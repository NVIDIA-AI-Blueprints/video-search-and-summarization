#!/bin/bash

# Note: On Mac the `platform not fully supported` warning is always printed, leading to the build.sh to return early and the web server is never started.
if [[ "$(uname)" != "Darwin" ]]; then
    # Exit script on error
    set -e
fi

SCRIPT_DIR=$(dirname ${BASH_SOURCE})

bash build.sh "$@"

echo "Docs: http://localhost:8077"

$SCRIPT_DIR/tools/packman/python.sh -m http.server --directory $SCRIPT_DIR/_build/docs 8077