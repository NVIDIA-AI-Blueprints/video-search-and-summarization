# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.

FROM nvcr.io/nvidia/base/ubuntu:noble-20260217 AS pybase
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
    apt-get install  -q -y apt-utils unzip  openjdk-11-jdk  libsnappy-dev language-pack-en

RUN apt install -y --only-upgrade openssl libssl-dev

RUN rm /usr/bin/python3
RUN ln -s /usr/bin/python3.13 /usr/bin/python3

FROM pybase AS main
# ENV SPARK_VERSION=2.4.4
# ENV HADOOP_VERSION=2.7
# RUN mkdir -pv /var/{log,run}/gunicorn/
RUN mkdir -pv /var/log/gunicorn/ && mkdir /var/run/gunicorn
# RUN chown -cR ubuntu:ubuntu /var/{log,run}/gunicorn/
#Download the Spark binaries from the repo
WORKDIR /

# RUN curl -sL --retry 3 \
#   "https://archive.apache.org/dist/spark/spark-2.4.4/spark-2.4.4-bin-hadoop2.7.tgz" \
#   | gunzip \
#   | tar x -C /usr/

# RUN mv /usr/spark-2.4.4-bin-hadoop2.7 /usr/spark && \
#   echo "export PATH=$PATH:/usr/spark/bin" >> ~/.bashrc


# Adding backend directory to make absolute filepaths consistent across services
RUN mkdir -pv /calibration/server/data
WORKDIR /calibration
# install nginx
RUN apt-get update && apt-get install nginx -y
# copy our nginx configuration to overwrite nginx defaults
COPY calibration/server/nginx/nginx.conf /etc/nginx/conf.d/default.conf
COPY calibration/server/nginx/nginx_default.conf /etc/nginx/nginx.conf

# link nginx logs to container stdout
RUN ln -sf /dev/stdout /var/log/nginx/access.log && ln -sf /dev/stderr /var/log/nginx/error.log

# Install Python dependencies
#RUN mkdir /var/log/gunicorn/ && mkdir /var/run/gunicorn
# COPY calibration/server/deploy.sh  /usr/local/bin
# RUN chmod 755 /usr/local/bin/deploy.sh
# COPY calibration/server/manage.py /usr/local/bin
# RUN chmod 755 /usr/local/bin/manage.py
COPY calibration/server/config/gunicorn/dev.py /
COPY calibration/server/resources/crs_config.json /
COPY 3rdParty_Licenses.md /
COPY 3rd_Party_Notice /


# Install pipenv and wheel 
RUN pip3 install pipenv wheel

# Copy project files including Pipfile and Pipfile.lock
COPY Pipfile* ./

# Install from the committed Pipfile.lock for fully reproducible builds.
# Hashes are emitted from the lock and verified by pip.
RUN pipenv requirements --hash > requirements.txt && \
    pip3 install --no-deps --no-cache-dir -r requirements.txt

ENV DEBUG="True"
ENV MULTIPROCESSING_ABSTRACT_SOCKETS_SUPPORTED="False"

COPY dist/calibration-1.0-py3-none-any.whl /
RUN pip3 install --upgrade pip /calibration-1.0-py3-none-any.whl

#COPY requirements.txt /app/server
#RUN pip3 install --upgrade pip -r requirements.txt

# Add the rest of the code
#COPY . /app/server

# RUN python3 -m pip install --upgrade pip setuptools==80.6.0 wheel
RUN pip3 install --index-url https://urm.nvidia.com/artifactory/api/pypi/sw-metropolis-pypi/simple vss-behavior-analytics==3.2.0

RUN pip3 uninstall -y wheel pipenv pip setuptools && \
    rm -r /root/.cache/pip



WORKDIR /calibration/

# patch with old version
RUN python3 - <<'PY'
import glob
for p in glob.glob('/usr/local/lib/python3.*/dist-packages/calibration/server/frontend/static/js/main*.js'):
    s = open(p, encoding='utf-8', errors='surrogateescape').read()
    open(p, 'w', encoding='utf-8', errors='surrogateescape').write(s.replace('"weekly"', '"3.64"'))
PY

# Make port 8000 available for the app
EXPOSE 8003
#EXPOSE 3000
# Be sure to use 0.0.0.0 for the host within the Docker container,
# otherwise the browser won't be able to find it

# RUN python3 /app/server/manage.py makemigrations
# RUN python3 /app/server/manage.py migrate
# CMD python3 /app/server/manage.py runserver 0.0.0.0:8000
