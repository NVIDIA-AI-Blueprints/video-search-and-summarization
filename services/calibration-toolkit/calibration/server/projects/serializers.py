# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from rest_framework import serializers
from calibration.server.projects.models import Intersection, Project, Sensor, City, Corridor, MMS, Place, PlaceTypes

class SensorSerializer(serializers.ModelSerializer):
    mapAPIKey = serializers.CharField(source='project.mapAPIKey', read_only=True)
    # imageUrl = serializers.ImageField(use_url=False, read_only=True)
    # invertImageUrl = serializers.ImageField(use_url=False, read_only=True)
    # floorPlanImageUrl = serializers.ImageField(use_url=False, read_only=True)

    class Meta:
        fields = '__all__'
        model = Sensor

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if instance.imageUrl:
            # Customize the image URL representation here
            representation['imageUrl'] = instance.imageUrl.url
        if instance.invertImageUrl:
            # Customize the image URL representation here
            representation['invertImageUrl'] = instance.invertImageUrl.url
        if instance.floorPlanImageUrl:
            representation['floorPlanImageUrl'] = instance.floorPlanImageUrl.url
        return representation

class PlaceSerializer(serializers.ModelSerializer):
    sensor_set = SensorSerializer(many=True, read_only=True)

    class Meta:
        fields = '__all__'
        model = Place


class PlaceTypesSerializer(serializers.ModelSerializer):
    place_set = PlaceSerializer(many=True, read_only=True)

    class Meta:
        fields = '__all__'
        model = PlaceTypes


class IntersectionSerializer(serializers.ModelSerializer):
    sensor_set = SensorSerializer(many=True, read_only=True)
    mapAPIKey = serializers.CharField(source='project.mapAPIKey', read_only=True)

    class Meta:
        fields = '__all__'
        model = Intersection


class CorridorSerializer(serializers.ModelSerializer):
    sensor_set = SensorSerializer(many=True, read_only=True)
    mapAPIKey = serializers.CharField(source='project.mapAPIKey', read_only=True)

    class Meta:
        fields = '__all__'
        model = Corridor


class CitySerializer(serializers.ModelSerializer):
    sensor_set = SensorSerializer(many=True, read_only=True)
    placeTypes_set = PlaceTypesSerializer(many=True, read_only=True)
    corridor_set = CorridorSerializer(many=True, read_only=True)
    intersection_set = IntersectionSerializer(
        many=True, read_only=True)
    # floorPlanImageUrl = serializers.ImageField(use_url=False)

    class Meta:
        fields = '__all__'
        model = City

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if instance.floorPlanImageUrl:
            representation['floorPlanImageUrl'] = instance.floorPlanImageUrl.url
        return representation


class ProjectSerializer(serializers.ModelSerializer):
    sensor_set = SensorSerializer(many=True, read_only=True)
    intersection_set = IntersectionSerializer(
        many=True, read_only=True)
    city_set = CitySerializer(many=True, read_only=True)
    placeTypes_set = PlaceTypesSerializer(many=True, read_only=True)
    corridor_set = CorridorSerializer(many=True, read_only=True)
#

    class Meta:
        fields = '__all__'
        model = Project

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if instance.floorPlanImageUrl:
            representation['floorPlanImageUrl'] = instance.floorPlanImageUrl.url
        return representation


###TODO do i need to add something here?
class MMSSerializer(serializers.ModelSerializer):
    sensor_set = SensorSerializer(many=True, read_only=True)
    # intersection_set = IntersectionSerializer(
    #     many=True, read_only=True)
    # city_set = CitySerializer(many=True, read_only=True)

    class Meta:
        fields = '__all__'
        model = MMS


