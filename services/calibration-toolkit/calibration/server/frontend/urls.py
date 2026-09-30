# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.

from django.urls import path, re_path

from . import views

urlpatterns = [
    path('', views.frontendView),
    re_path(r'^help/.*$', views.frontendView),
    re_path(r'^projects/.*$', views.frontendView),
    re_path(r'^calib/.*$', views.frontendView),
    re_path(r'^links/.*$', views.frontendView),
    re_path(r'^validation/.*$', views.frontendView),
    re_path(r'^corridor/.*$', views.frontendView),
    # Catch-all pattern for SPA
    re_path(r'^.*$', views.frontendView),
]
