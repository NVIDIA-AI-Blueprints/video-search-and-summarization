# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# import os
"""Gunicorn *development* config file"""



# os.environ.setdefault("DJANGO_SETTINGS_MODULE", "calibration.server.server.settings")

# Django WSGI application path in pattern MODULE_NAME:VARIABLE_NAME
wsgi_app = "calibration.server.server.wsgi:calibration-toolkit"
# The granularity of Error log outputs
loglevel = "debug"
# The number of worker processes for handling requests
workers = 3
# The socket to bind
bind = "0.0.0.0:8004"
# Restart workers when code changes (development only!)
reload = True
# Write access and error info to /var/log
accesslog = errorlog = "/var/log/gunicorn/dev.log"
# Redirect stdout/stderr to log file
capture_output = True
# PID file so you can easily fetch process ID
# pidfile = "/var/run/gunicorn/dev.pid"
# Daemonize the Gunicorn process (detach & enter background)
daemon = True
#timeout time
timeout = 1000