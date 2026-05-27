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

FROM nvcr.io/nvidia/cudagl:11.4.1-runtime-ubuntu18.04

RUN rm /etc/apt/sources.list.d/cuda.list

RUN apt-get update && apt-get upgrade -y

RUN DEBIAN_FRONTEND=noninteractive  apt-get install -y --no-install-recommends \
	curl \
	vim \
	iputils-ping \
	wget \
	dialog \
	net-tools
RUN apt-get remove -y nodejs npm
RUN curl -sL deb.nodesource.com/setup_12.x | bash -
RUN apt-get install -y nodejs 
RUN apt-get install -y xvfb firefox

RUN gpg --keyserver keyserver.ubuntu.com --recv-key 409B6B1796C275462A1703113804BB82D39DC0E3 7D2BAF1CF37B13E2069D6956105BD0E739499BDB
RUN curl -sSL https://get.rvm.io | bash -s stable

RUN apt-get install -y rubygems
RUN /usr/local/rvm/bin/rvm install ruby 3.0
RUN /usr/bin/gem install bundler

ARG PKG_LOCATION

ADD ${PKG_LOCATION}/vst_test_package /home/vst_test_package

RUN mv /home/vst_test_package/geckodriver /usr/local/bin
RUN chmod +x /home/vst_test_package/vst_test.sh
RUN chmod +x /usr/local/bin/geckodriver

# Set the default command to execute script
ENTRYPOINT ["/bin/bash", "/home/vst_test_package/vst_test.sh"]
