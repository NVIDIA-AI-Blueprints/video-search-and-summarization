#!/bin/bash
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0


# Collect static files
# echo "Collect static files"
# python3 manage.py collectstatic --noinput
echo $PWD
# Apply database migrations

export DEBUG="True"
# pip3 install -e .

manage.py makemigrations

echo "Apply database migrations"
manage.py migrate

# Start server
echo "Starting server"
manage.py runserver 0.0.0.0:8000