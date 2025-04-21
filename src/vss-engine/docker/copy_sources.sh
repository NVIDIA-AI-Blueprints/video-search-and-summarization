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

set -e

if [ "$#" -ne 2 ]; then
    echo "Error: Exactly two arguments required."
    echo "Usage: $(basename $0) <via-src-dir> <out-dir>"
    exit 1
fi

VIA_SRC_DIR=$(realpath $1)
OUT_DIR=$(realpath $2)
SCRIPT_DIR=$(dirname $(realpath $0))

FILE_LIST="$SCRIPT_DIR/package_file_list.txt"

while read -r file; do
    if [[ $file = \#* ]] ; then
        continue
    fi
    SRC_FILE="$VIA_SRC_DIR/$file"
    DEST_FILE="$OUT_DIR/via-engine/$file"
    DEST_DIR=$(dirname $DEST_FILE)
    mkdir -p "$DEST_DIR"
    cp -v "$SRC_FILE" "$DEST_FILE"
done < $FILE_LIST

cp -v "$VIA_SRC_DIR/via_client_cli.py" "$OUT_DIR"