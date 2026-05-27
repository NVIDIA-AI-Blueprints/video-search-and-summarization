#!/bin/sh

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

# Default to 1 instance if NUMBER_OF_NVSTREAMER_INSTANCES is not set
INSTANCES=${NUMBER_OF_NVSTREAMER_INSTANCES:-1}
PODNS=${POD_NAMESPACE:-default}

cat << EOF > /etc/nginx/nginx.conf
worker_processes auto;

error_log  /tmp/nginx/error.log warn;
pid        /tmp/nginx/nginx.pid;

events {
    worker_connections 1024;
}

http {
    client_body_temp_path /tmp/nginx/client-body;
    proxy_temp_path       /tmp/nginx/proxy;
    fastcgi_temp_path     /tmp/nginx/fastcgi;
    uwsgi_temp_path       /tmp/nginx/uwsgi;
    scgi_temp_path        /tmp/nginx/scgi;

    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    log_format main '\$remote_addr - \$remote_user [\$time_local] "\$request" '
                    '\$status \$body_bytes_sent "\$http_referer" '
                    '"\$http_user_agent" "\$http_x_forwarded_for"';

    # WebSocket proxy settings
    map \$http_upgrade \$connection_upgrade {
        default upgrade;
        '' close;
    }

    access_log /tmp/nginx/access.log main;

    sendfile        on;
    keepalive_timeout  65;

    server {
        listen 8000;
        listen  [::]:8000;

        location /health {
            return 200 'ok';
            add_header Content-Type text/plain;
        }

        location /nvstreamer/ {
            rewrite ^/nvstreamer/(.*) /\$1 break;
            proxy_pass http://nvstreamer-instance-deployment-0.nvstreamer-instance-deployment-svc.$PODNS:31000;
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_http_version 1.1;
            client_max_body_size 5000M;

            # WebSocket specific settings
            proxy_set_header Upgrade \$http_upgrade;
            proxy_set_header Connection \$connection_upgrade;

            # Timeout settings
            proxy_read_timeout 3600s;  # 1 hour
            proxy_send_timeout 3600s;  # 1 hour
        }

EOF

# Generate location blocks for additional instances
for i in $(seq 1 $(($INSTANCES-1))); do
    cat << EOF >> /etc/nginx/nginx.conf
        location /nvstreamer-$i/ {
            rewrite ^/nvstreamer-$i/(.*) /\$1 break;
            proxy_pass http://nvstreamer-instance-deployment-$i.nvstreamer-instance-deployment-svc.$PODNS:31000;
            proxy_set_header Host \$host;
            proxy_set_header X-Real-IP \$remote_addr;
            proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
            proxy_http_version 1.1;
            client_max_body_size 5000M;

            # WebSocket specific settings
            proxy_set_header Upgrade \$http_upgrade;
            proxy_set_header Connection \$connection_upgrade;

            # Timeout settings
            proxy_read_timeout 3600s;  # 1 hour
            proxy_send_timeout 3600s;  # 1 hour
        }

EOF
done

# Close the server and http blocks
echo "    }
}" >> /etc/nginx/nginx.conf

# Output the generated configuration
cat /etc/nginx/nginx.conf

# Start NGINX
exec nginx -g 'daemon off;'

