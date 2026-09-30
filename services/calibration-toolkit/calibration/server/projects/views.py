# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
from rest_framework import generics
from rest_framework.decorators import api_view
from calibration.server.server.settings import BASE_DIR, STATIC_HOST, STATIC_URL,FORCE_SCRIPT_NAME

from django.http import HttpResponse, HttpResponseBadRequest

from django.views.decorators.csrf import csrf_exempt
from calibration.server.projects.models import Intersection, Project, Sensor, City, Corridor, Place, PlaceTypes, MMS
from calibration.server.projects.homography import getHomographyMatrix
from calibration.server.projects.approxHomography import getApproxHomographyMatrix
from calibration.server.projects.rtspScreenshot import getRtspScreenshot
from calibration.server.projects.roadSegment import getRoadSegment
from calibration.server.projects.downloadOSM import getOSMFile
from calibration.server.projects.invertImage import createInvertedImage
from calibration.server.projects.uploadWebApi import uploadData
from calibration.server.projects.syncFloorPlan import syncFloorPlanFiles
from calibration.server.projects.importProject import importProject
from calibration.server.projects.discoverSensors import getSensors
from calibration.server.projects.serializers import \
            IntersectionSerializer, ProjectSerializer, \
            SensorSerializer, CitySerializer, CorridorSerializer, \
            PlaceSerializer, PlaceTypesSerializer, MMSSerializer
from calibration.server.projects.packageInvertedImages import getWarpedFiles
from calibration.server.projects.packageFloorPlanImages import \
    getFloorPlanFiles
from calibration.server.projects.packageImages import getImageFiles
from calibration.server.projects.syncSensorInfo import syncSensorInfo
import logging

logger = logging.getLogger(__name__)

class ListIntersections(generics.ListCreateAPIView):
    queryset = Intersection.objects.all()
    serializer_class = IntersectionSerializer


class DetailIntersection(generics.RetrieveUpdateDestroyAPIView):
    queryset = Intersection.objects.all()
    serializer_class = IntersectionSerializer


class ListProjects(generics.ListCreateAPIView):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        # print(f'Response list data: {response.data}')
        # logger.info(f'Response list1 data: {response.data}')
        return response

class DetailProject(generics.RetrieveUpdateDestroyAPIView):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        # print(f'Response detail data: {response.data}')
        # logger.info(f'Response detail1 data: {response.data}')
        return response

class ListSensors(generics.ListCreateAPIView):
    queryset = Sensor.objects.all()
    serializer_class = SensorSerializer

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        # print(f'Response data: {response.data}')
        return response

class DetailSensor(generics.RetrieveUpdateDestroyAPIView):
    queryset = Sensor.objects.all()
    serializer_class = SensorSerializer

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        # print(f'Response data: {response.data}')
        return response


class ListCities(generics.ListCreateAPIView):
    queryset = City.objects.all()
    serializer_class = CitySerializer


class DetailCity(generics.RetrieveUpdateDestroyAPIView):
    queryset = City.objects.all()
    serializer_class = CitySerializer


class ListCorridors(generics.ListCreateAPIView):
    queryset = Corridor.objects.all()
    serializer_class = CorridorSerializer


class DetailCorridor(generics.RetrieveUpdateDestroyAPIView):
    queryset = Corridor.objects.all()
    serializer_class = CorridorSerializer


class ListPlaces(generics.ListCreateAPIView):
    queryset = Place.objects.all()
    serializer_class = PlaceSerializer


class DetailPlace(generics.RetrieveUpdateDestroyAPIView):
    queryset = Place.objects.all()
    serializer_class = PlaceSerializer


class ListPlaceTypes(generics.ListCreateAPIView):
    queryset = PlaceTypes.objects.all()
    serializer_class = PlaceTypesSerializer


class DetailPlaceType(generics.RetrieveUpdateDestroyAPIView):
    queryset = PlaceTypes.objects.all()
    serializer_class = PlaceTypesSerializer


def approxHomography(request, pk):
    url = request.path
    getApproxHomographyMatrix(url)
    return HttpResponse('Homography matrix found')


def homography(request, pk):
    url = request.path
    getHomographyMatrix(url)
    return HttpResponse('Homography matrix found')


def rtspScreenshot(request, pk):
    url = request.path
    getRtspScreenshot(url)
    return HttpResponse('Image uploaded via rtsp')


def roadSegment(request):
    url = request.path
    getRoadSegment(url)
    return HttpResponse('Road Network Generated')


def downloadOSM(request):
    url = request.path
    getOSMFile(url)
    return HttpResponse('OSM File Downloaded')

@api_view(['GET'])
def importSensors(request):
    url = request.path
    response = getSensors(url)
    logger.debug("import sensors %s", response)
    return response
    # return HttpResponse('Sensors Import ed', response)


def uploadFiles(request):
    url = request.path
    response = importProject(url)
    return HttpResponse(response)


def uploadWebApi(request):
    url = request.path
    response = uploadData(url)
    return response


def invertImage(request, pk):
    url = request.path
    createInvertedImage(url)
    return HttpResponse('Image Inverted')


def syncFloorPlan(request):
    url = request.path
    syncFloorPlanFiles(url)
    return HttpResponse('Floorplan Synced')


def syncSensor(request, pk):
    url = request.path
    syncSensorInfo(url)
    return HttpResponse('Sensor Info Synced')


@csrf_exempt
@api_view(['POST'])
def getWarpedImages(request, pk):
    url = request.path
    resp = getWarpedFiles(url)
    return resp

@csrf_exempt
@api_view(['POST'])
def getFloorPlanImages(request, pk):
    url = request.path
    resp = getFloorPlanFiles(url)
    return resp

@csrf_exempt
@api_view(['POST'])
def getImages(request, pk):
    url = request.path
    resp = getImageFiles(url)
    return resp


# def home(request):
#     x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
#     if x_forwarded_for:
#         ip = x_forwarded_for.split(',')[0]
#     else:
#         ip = request.META.get('REMOTE_ADDR')
#     logging.debug('ip address of the client = ' , ip)
#     pod_name = os.environ.get('HOSTNAME')


#     return HttpResponse(
#         '<h1>Hello World . ip address of the client = ' + str(ip) + '</h1>'
#         '<h1>Hello World . pod name = ' + str(pod_name) + '</h1>'
#         )


# def page(request):
#     x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
#     if x_forwarded_for:
#         ip = x_forwarded_for.split(',')[0]
#     else:
#         ip = request.META.get('REMOTE_ADDR')
#     logging.debug('ip address of the client = ' , ip)
#     pod_name = os.environ.get('HOSTNAME')

#     ()

#     return HttpResponse(
#         '<h1>Hello World . ip address of the client = ' + str(ip) + str(x_forwarded_for) + '</h1>'
#         '<h1>Hello World . pod name = ' + str(pod_name) + '</h1>'
#         '<h1>Hello World . pod name = ' + str(BASE_DIR) + '</h1>'
#         '<h1>Hello World . pod name = ' + str(STATIC_URL) + str(FORCE_SCRIPT_NAME) + '</h1>'
#         )