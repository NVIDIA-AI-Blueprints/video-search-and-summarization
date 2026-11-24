#!/bin/bash
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

IN_DIR=
OUT_FILE=
PREFIX=

while [[ $# -gt 0 ]]
do
key="$1"

case $key in
    -i|--input)
    IN_DIR="$2"
    shift # past argument
    shift # past value
    ;;
    -o|--outfile)
    OUT_FILE="$2"
    shift # past argument
    shift # past value
    ;;
    -p|--prefix)
    PREFIX="$2"
    shift # past argument
    shift # past value
    ;;
esac
done

declare -A IDMAP
IDMAP=(
    [Person]=1
    [Car]=0
    [Bicycle]=0
    [Roadsign]=0
    [Bag]=0
)

usage() {
    echo "Usage: $0 -i <input kitti labels dir> -o <output file name> [-p <prefix>]"
    echo ""
    echo "Ex: $0 -i ~/deepstream/labels -o ~/output/mot-labels.txt -p 00_000"
    echo "This converts all labels in ~/deepstream/labels/00_000*.txt into MOT labels to be stored in ~/output/mot-labels.txt"
}

[[ -z "$IN_DIR" ]] && usage && exit -1
[[ ! -d "$IN_DIR" ]] && echo "$IN_DIR is not a directory" && exit -1
[[ -z "$OUT_FILE" ]] && usage && exit -1

> $OUT_FILE
i=0
while IFS= read -r -d '' IN_FILE
do
    [[ "$IN_FILE" == "$OUT_FILE" ]] && continue
    while IFS=" " read -r f2 f3 f4 f5 f6 f7 f8 f9 f10 f11 f12 f13 f14 f15 f16 f17 f18
    do
	    class=$f2
        class_id=${IDMAP[$class]}
	    if [[ "$class_id" == 0 ]]; then
            continue
        fi
    	frame=$i
	    id=-1
    	occl=$f4
    	x1=${f6%.*}
    	y1=${f7%.*}
    	x2=${f8%.*}
    	y2=${f9%.*}
        width=$((x2-x1))
        height=$((y2-y1))
        confidence=$f17
        printf "%u,%d,%d,%d,%d,%d,%f,-1,-1,-1\n" \
	       "$frame" "$id" "$x1" "$y1" "$width" "$height" "$confidence" >> $OUT_FILE
    done < "$IN_FILE"
    ((i++))
done < <(find "$IN_DIR" -maxdepth 1 -name "$PREFIX*.txt" -type f -print0 | sort -zV)

((i--))
echo "Done converting $i frames"
