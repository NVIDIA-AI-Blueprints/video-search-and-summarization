#!/usr/bin/env sh
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

arch=$(dpkg --print-architecture)

# Check if a JSON file is provided as an argument
if [ $# -ne 1 ]; then
    echo "Usage: $0 <json-file>"
    exit 1
fi

# Read the JSON file
json_file=$1

# Install jq if not installed
if ! command -v jq &> /dev/null; then
    echo "jq not found. Downloading jq..."
    
    # Download jq
    if wget https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-linux-${arch} -O /tmp/jq ; then
        echo "Downloaded using wget"
    elif curl -sSL https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-linux-${arch} --output /tmp/jq; then
        echo "Downloaded using curl"
    else
        echo "Unable to download jq! The container is missing wget/curl"
        exit 1
    fi
    chmod +x /tmp/jq 
fi


mkdir -p /lib/distroless
echo "Copying binaries..."

binaries=$(/tmp/jq -r '.binaries[]' "$json_file")
for binary in $binaries; do
    echo "Processing binary ${binary}"
    if echo "${binary}" | grep -q "*"; then
        package_info=$(dpkg -S $(find $(dirname "$binary") -name $(basename "$binary") | head -n 1))
    else
        package_info=$(dpkg -S "$binary")
    fi
    package_name=$(echo "$package_info" | awk -F ':' '{print $1}')
    echo "Package name is ${package_name}"
    
    # Get package version
    dpkg_output=$(dpkg -l "$package_name")
    version=$(echo "$dpkg_output" | grep "^ii" | awk '{print $3}')
    echo "Package version is ${version}"
    
    # Save package info to /var/lib/dpkg/status.d/<package>_<version>
    mkdir -p /var/lib/dpkg/status.d/
    apt show "$package_name" > "/var/lib/dpkg/status.d/${package_name}_${version}" 2> /dev/null
    echo "Wrote package info to /var/lib/dpkg/status.d/${package_name}_${version}"
    
    # extract the abs library path with ldd for that binary
    libraries=$(ldd $binary | grep -Po "> .*\(" |  cut -d '>' -f 2 | cut -d '(' -f 1)
    echo $libraries
    for lib in $libraries; do
        echo "copying ${lib} to /lib/distroless"
        cp ${lib}  /lib/distroless
        if echo "$lib" | grep -q "*"; then
            package_info=$(dpkg -S $(find $(dirname "$lib") -name $(basename "$lib") | head -n 1))
            if [[ -z $package_info ]]; then
                package_info=$(dpkg -S $(basename $(find $(dirname "$lib") -name $(basename "$lib") | head -n 1)) | head -n 1)
            fi
        else
            package_info=$(dpkg -S "$lib")
            if [[ -z $package_info ]]; then
                package_info=$(dpkg -S $(basename "$lib") | head -n 1)
            fi
        fi
        package_name=$(echo "${package_info}" | awk -F ':' '{print $1}')
        echo "Package name is $package_name"
        
        # Get package version
        dpkg_output=$(dpkg -l "${package_name}")
        version=$(echo "$dpkg_output" | grep "^ii" | awk '{print $3}')
        echo "Package version is ${version}"
        
        # Save package info to /var/lib/dpkg/status.d/<package>_<version>
        mkdir -p /var/lib/dpkg/status.d/
        apt show "$package_name" > "/var/lib/dpkg/status.d/${package_name}_${version}" 2> /dev/null
        echo "Wrote package info to /var/lib/dpkg/status.d/${package_name}_${version}"
    done
done

echo "Installing Debian packages"

packages=$(/tmp/jq -r '.packages[]' "$json_file")
for package in $packages; do
    echo "Installing ${package}"
    apt-get update -qq
    apt-get install -y --no-install-recommends "$package"

    # Parse package name and get installed version
    package_name=$(echo "$package" | cut -d '=' -f 1)
    dpkg_output=$(dpkg -l "$package_name")
    version=$(echo "$dpkg_output" | grep "^ii" | awk '{print $3}')
    echo "Installed ${package_name} version ${version}"

    # Save package metadata
    mkdir -p /var/lib/dpkg/status.d/
    apt show "$package_name" > "/var/lib/dpkg/status.d/${package_name}_${version}" 2> /dev/null
    echo "Wrote package info to /var/lib/dpkg/status.d/${package_name}_${version}"

    # Copy all files installed by the package (binaries, libs, etc.)
    installed_files=$(dpkg -L "$package_name")
    for file in $installed_files; do
        # Skip directories
        [ ! -f "$file" ] && continue

        if echo "$file" | grep -qE '\.so(\..*)?$'; then
            echo "Copying library ${file} to /lib/distroless"
            cp "$file" /lib/distroless/
        else
            echo "Copying file ${file}"
            mkdir -p "$(dirname "$file")"
            cp "$file" "$file" 2>/dev/null || true
        fi
    done

    # Resolve shared library dependencies for all ELF binaries/libs from this package
    for file in $installed_files; do
        [ ! -f "$file" ] && continue
        # Skip non-ELF files
        file_type=$(file -b "$file")
        echo "$file_type" | grep -q "ELF" || continue

        echo "Resolving dependencies for ${file}"
        libraries=$(ldd "$file" 2>/dev/null | grep -Po "> .*\(" | cut -d '>' -f 2 | cut -d '(' -f 1)
        for lib in $libraries; do
            echo "Copying dependency ${lib} to /lib/distroless"
            cp ${lib} /lib/distroless/ 2>/dev/null || true
            if echo "$lib" | grep -q "*"; then
                dep_package_info=$(dpkg -S $(find $(dirname "$lib") -name $(basename "$lib") | head -n 1))
                if [ -z "$dep_package_info" ]; then
                    dep_package_info=$(dpkg -S $(basename $(find $(dirname "$lib") -name $(basename "$lib") | head -n 1)) | head -n 1)
                fi
            else
                dep_package_info=$(dpkg -S "$lib")
                if [ -z "$dep_package_info" ]; then
                    dep_package_info=$(dpkg -S $(basename "$lib") | head -n 1)
                fi
            fi
            dep_package_name=$(echo "${dep_package_info}" | awk -F ':' '{print $1}')
            echo "Dependency package name is $dep_package_name"

            # Get dependency package version
            dep_dpkg_output=$(dpkg -l "${dep_package_name}")
            dep_version=$(echo "$dep_dpkg_output" | grep "^ii" | awk '{print $3}')
            echo "Dependency package version is ${dep_version}"

            # Save dependency package metadata
            mkdir -p /var/lib/dpkg/status.d/
            apt show "$dep_package_name" > "/var/lib/dpkg/status.d/${dep_package_name}_${dep_version}" 2> /dev/null
            echo "Wrote package info to /var/lib/dpkg/status.d/${dep_package_name}_${dep_version}"
        done
    done
done