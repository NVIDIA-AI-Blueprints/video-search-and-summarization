# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

FROM nvcr.io/nvidia/base/ubuntu:noble-20260217
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Check-Date=false update -y

# Install Python 3.13 from deadsnakes PPA without `software-properties-common`
# so we avoid pulling in `packagekit` (CVE-2026-41651) and stale
# `python3-cryptography 3.4.8` (multiple CVEs).
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates curl wget gnupg && \
    install -d /etc/apt/keyrings && \
    curl -fsSL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0xF23C5A6CF475977595C89F51BA6932366A755776" \
        | gpg --dearmor > /etc/apt/keyrings/deadsnakes.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/deadsnakes.gpg] https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu noble main" \
        > /etc/apt/sources.list.d/deadsnakes.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.13 python3.13-dev python3.13-venv \
        gcc build-essential && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.13 1 && \
    rm -rf /var/lib/apt/lists/*

# Install Pip - get-pip.py is piped via stdin so it never lands on disk;
# `-fsSL` makes curl fail fast on HTTP errors instead of piping an HTML
# error page into python3.
RUN curl -fsSL https://bootstrap.pypa.io/get-pip.py | python3 - "pip==23.1.2" "setuptools==80.6.0"

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -q -y apt-utils unzip  openjdk-11-jdk  libsnappy-dev language-pack-en nodejs

RUN apt install -y --only-upgrade openssl libssl-dev

RUN rm /usr/bin/python3
RUN ln -s /usr/bin/python3.13 /usr/bin/python3

# RUN apt-get update && \
#     apt-get upgrade -y && \
#     apt-get install -q -y apt-utils curl unzip  openjdk-11-jdk  python3-pip libsnappy-dev language-pack-en
# RUN apt update && apt install --only-upgrade openssl libssl-dev


# ENV SPARK_VERSION=2.4.4
# ENV HADOOP_VERSION=2.7

# #Download the Spark binaries from the repo
# WORKDIR /

# RUN curl -sL --retry 3 \
#   "https://archive.apache.org/dist/spark/spark-2.4.4/spark-2.4.4-bin-hadoop2.7.tgz" \
#   | gunzip \
#   | tar x -C /usr/

# RUN mv /usr/spark-2.4.4-bin-hadoop2.7 /usr/spark && \
#   echo "export PATH=$PATH:/usr/spark/bin" >> ~/.bashrc

# Adding backend directory to make absolute filepaths consistent across services
WORKDIR /calibration/server

# Install Python dependencies
COPY requirements.txt /calibration/server
RUN pip3 install --upgrade pip -r requirements.txt
ENV DEBUG="True"
COPY ./dist/calibration-0.1-py3-none-any.whl /calibration/server
RUN pip3 install --upgrade pip /calibration-0.1-py3-none-any.whl

# Add the rest of the code
# COPY . /calibration/server

WORKDIR /calibration/server


# Make port 8000 available for the app
EXPOSE 8003
EXPOSE 81
# Be sure to use 0.0.0.0 for the host within the Docker container,
# otherwise the browser won't be able to find it

# RUN python3 /app/server/manage.py makemigrations
# RUN python3 /app/server/manage.py migrate
# CMD python3 /app/server/manage.py runserver 0.0.0.0:8000