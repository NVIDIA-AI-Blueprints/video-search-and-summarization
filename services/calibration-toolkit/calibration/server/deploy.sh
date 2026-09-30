#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

PORT="${1:-8000}"
SCRIPT_NAME="${2:-""}"
# Collect static files
# echo "Collect static files"
export SUBDIRECTORY=$SCRIPT_NAME
export DEBUG=False

echo "SubDirectory:" $SUBDIRECTORY
manage.py collectstatic --noinput
echo $PWD
# Apply database migrations
echo "Apply database migrations"
manage.py makemigrations
manage.py migrate



# Create superuser
echo "Create super user"
DJANGO_SUPERUSER_EMAIL=django@admin.com
DJANGO_SUPERUSER_USERNAME=django
# DJANGO_SUPERUSER_PASSWORD=admin

DJANGO_SUPERUSER_PASSWORD=admin manage.py createsuperuser --noinput --username $DJANGO_SUPERUSER_USERNAME --email $DJANGO_SUPERUSER_EMAIL

# Start server
echo "Starting server"
#manage.py runserver 0.0.0.0:8000
# gunicorn --print-config calibration.server.server.wsgi

# gunicorn --timeout 1000 --bind :${PORT} --workers 3 calibration.server.server.wsgi --daemon
# gunicorn --timeout 1000 --bind :8000 --workers 3 calibration.server.server.wsgi --daemon  --error-logfile /var/log/gunicorn/error.log  --access-logfile /var/log/gunicorn/access.log
gunicorn -c /dev.py calibration.server.server.wsgi

echo "Server Running.. Navigate to Application Address"

nginx -g 'daemon off;'
