# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from django.urls import path, re_path

from calibration.server.projects import views
# from calibration.server.projects.views import home, page
urlpatterns = [
    # path('home/', home, name='home'),
    # path('page/', page, name='page'),
    path('intersections/', views.ListIntersections.as_view()),
    path('intersections/<int:pk>/', views.DetailIntersection.as_view()),
    path('projects/', views.ListProjects.as_view()),
    path('projects/<int:pk>/', views.DetailProject.as_view()),
    path('sensors/', views.ListSensors.as_view()),
    path('sensors/<uuid:pk>/', views.DetailSensor.as_view()),
    path('cities/', views.ListCities.as_view()),
    path('cities/<int:pk>/', views.DetailCity.as_view()),
    path('corridors/', views.ListCorridors.as_view()),
    path('corridors/<int:pk>/', views.DetailCorridor.as_view()),
    path('places/', views.ListPlaces.as_view()),
    path('places/<int:pk>/', views.DetailPlace.as_view()),
    path('placeTypes/', views.ListPlaceTypes.as_view()),
    path('placeTypes/<int:pk>/', views.DetailPlaceType.as_view()),

    path('homography/<uuid:pk>/', views.homography),
    path('approxHomography/<uuid:pk>/', views.approxHomography),
    path('rtsp/<uuid:pk>/', views.rtspScreenshot),
    path('invertImage/<uuid:pk>/', views.invertImage),
    path('syncSensor/<uuid:pk>/', views.syncSensor),

    re_path(r'^roadSegment/\d+/', views.roadSegment),
    re_path(r'^mapFile/\d+/', views.downloadOSM),
    re_path(r'^importSensors/\d+/', views.importSensors),
    re_path(r'^uploadWebApi/\d+/', views.uploadWebApi),
    re_path(r'^getWarpedFiles/\d+/', views.getWarpedFiles),
    re_path(r'^getFloorPlanFiles/\d+/', views.getFloorPlanFiles),
    re_path(r'^getImageFiles/\d+/', views.getImageFiles),
    re_path(r'^syncFloorPlan/\d+/', views.syncFloorPlan),
    re_path(r'^uploadFiles/\d+/', views.uploadFiles),
    # re_path(r'^homography/\d+/', views.homography),
    # re_path(r'^approxHomography/\d+/', views.approxHomography),

]
