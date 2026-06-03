#!/bin/bash

# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

echo -e "########################################################################################################"
echo -e "                            #     Video Storage Toolkit    #                                            "
echo -e "########################################################################################################"

usage(){
	echo -e "Usage:\n\n $0\n\t -d, --package_install_dir \e[4mPath to install vst package\e[0m\n\t -h, --help"
	
	exit 1
}

user=$(whoami)
SHORT=d:,h
LONG=package_install_dir:,help
OPTS=$(getopt -n vst --options $SHORT --longoptions $LONG -- "$@")

eval set -- "$OPTS"

while :
do
  case "$1" in
    -d | --package_install_dir )
      package_install_dir="$2"
      shift 2
      ;;
    -h | --help)
      usage
      ;;
    --)
      shift;
      break
      ;;
    *)
      echo "Unexpected option: $1"
      ;;
  esac
done

sudo apt update

# Install dependent packages.
pkgs='libcurl3-gnutls libcurl4 libxml2 libssl-dev libjsoncpp1 sqlite3 uuid iputils-ping gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-ugly gstreamer1.0-plugins-bad gstreamer1.0-libav libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 libboost-filesystem-dev libboost-regex-dev libboost-thread-dev libjansson-dev librdkafka-dev'

install=false
for pkg in $pkgs; do
  status="$(dpkg-query -W --showformat='${db:Status-Status}' "$pkg" 2>&1)"
  if [[ ! $? = 0 ]] || [[ ! "$status" == *"installed"* ]]; then
    install=true
    sudo apt install -y --no-install-recommends $pkg
  fi
done

echo -e "\n\n"
if [[ -z "$package_install_dir" ]]; then
  echo "package_install_dir = $(pwd)"
else
  echo "package_install_dir = $package_install_dir"
fi
echo -e "\n"

install_location=""
if [[ -n "$package_install_dir" ]]
then
  install_location=$package_install_dir/vst_release
else
  install_location=vst_release
fi

if [[ -d "$install_location" ]]; then
  echo "Directory already exists."
  while true; do
    read -p "Overwrite (y/N)? " -n 1
    echo
    case $REPLY in
      [Yy]* ) echo -e "\e[33mCurrent configuration of VST will be overwritten with default values\e[0m"; break;;
      [Nn]* ) sudo -k; exit 1;;
      * ) echo "Please answer yes or no.";;
    esac
  done
fi

# untar the package
if [[ -n "$package_install_dir" ]]
then
	sudo tar -xf vst_release.tbz2 -C $package_install_dir
else
	sudo tar -xf vst_release.tbz2
fi

sudo chown -R $user: $install_location
cd $install_location

# set the capabilities for non-privileged user.
sudo /sbin/setcap cap_net_admin,cap_net_raw,cap_net_bind_service=+eip launch_vst
sudo /sbin/setcap cap_net_admin,cap_net_raw,cap_dac_read_search=+ep /sbin/xtables-legacy-multi
sudo sysctl net.core.rmem_max=2000000 &> /dev/null
sudo chmod u+x launch_vst
sudo -k
