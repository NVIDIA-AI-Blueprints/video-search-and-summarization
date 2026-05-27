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

FROM ubuntu:24.04

# Install nginx and dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         nginx=1.24.0-2ubuntu7.8 \
         nginx-common=1.24.0-2ubuntu7.8 \
         ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r nginx \
    && useradd -r -g nginx -s /usr/sbin/nologin nginx

# Create necessary directories and set permissions
RUN mkdir -p /tmp/nginx/client-body \
             /tmp/nginx/proxy \
             /tmp/nginx/fastcgi \
             /tmp/nginx/uwsgi \
             /tmp/nginx/scgi \
             /tmp/nginx/cache \
    && mkdir -p /var/lib/nginx/body \
               /var/lib/nginx/proxy \
               /var/lib/nginx/fastcgi \
               /var/lib/nginx/uwsgi \
               /var/lib/nginx/scgi \
    && mkdir -p /var/log/nginx \
    && chown -R nginx:nginx /tmp/nginx \
    && chown -R nginx:nginx /var/lib/nginx \
    && chown -R nginx:nginx /var/log/nginx \
    && chmod -R 755 /tmp/nginx /var/lib/nginx /var/log/nginx

# Copy your custom nginx.conf
#COPY nginx.conf /etc/nginx/nginx.conf
COPY generate_nginx_conf.sh /usr/local/bin/generate_nginx_conf.sh
RUN chmod +x /usr/local/bin/generate_nginx_conf.sh

# Set the user to nginx
#USER nginx

# Expose the port Nginx is running on
EXPOSE 8000

CMD ["/usr/local/bin/generate_nginx_conf.sh"]
