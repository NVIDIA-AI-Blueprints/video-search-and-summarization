# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2009-2024. NVIDIA CORPORATION.  All rights reserved.
"""Resources root module."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from django.db import models
import uuid


# @receiver(m2m_changed, )
# def invalidate_cache_m2m(sender, instance, action, reverse, model, pk_set, **kwargs ):
#     if action in ['post_add', 'post_remove'] :
#         model.objects.invalidate(instance)

class TimeStampedModel(models.Model):
    """
    An abstract base class model that provides self-
    updating ``created`` and ``modified`` fields.
    """
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True


def uploadPath(instance, filename):
    return '/'.join(['{}/images'.format(instance.project.id), str(filename)])


def uploadInvPath(instance, filename):
    return '/'.join(['{}/inverted'.format(instance.project.id,), str(filename)])

def uploadImageFilesPath(instance, filename):
    return '/'.join(['{}/'.format(instance.id), str(filename)])


def uploadFloorPlanSensorPath(instance, filename):
    return '/'.join(['{}/floorplan'.format(instance.project.id), str(filename)])

def uploadFloorPlanPath(instance, filename):
    return '/'.join(['{}/floorplan'.format(instance.id), str(filename)])


class SafeCharField(models.CharField):
    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value:
            return value[:self.max_length]
        return value


CALIBRATION_CHOICES = (
                       ('geo', 'geo'),
                       ('cartesian', 'cartesian'),
                       ('floorplan', 'floorplan'),
                       ('mtmc', 'mtmc'),
                       ('image', 'image')
                     )


class Project(TimeStampedModel):
    id = models.AutoField(primary_key=True)
    name = models.CharField(
        max_length=200, default="INITIAL_PROJECT", unique=True)
    # class ProjectType(models.TextChoices):
    #     CARTESIAN = "CAR", _('Cartesian')
    #     GIS = "GIS", _('GIS')
    # projectType = models.CharField(
    #     max_length=3,
    #     choices=ProjectType.choices,
    #     default=ProjectType.GIS
    # )

    calibrationType = models.CharField(max_length=9,
                                       choices=CALIBRATION_CHOICES,
                                       default='geo')
    mapAPIKey = models.CharField(max_length=200, default="", blank=True)
    mapFile = models.TextField(blank=True, null=False)
    mapZoom = models.FloatField(default="18.0")
    mapCenter = models.TextField(blank=True, null=True)

    webApiUrl = models.TextField(default="")
    scaleFactor = models.FloatField(default="1.0")
    calibrationJsonTemp = models.TextField(default="")
    imageMetaDataJsonTemp = models.TextField(default="")
    imageFiles = models.FileField(blank=True, null=True,
                                  upload_to=uploadImageFilesPath)

    calibrationJson = models.TextField(default="")
    sensorMetadataCsv = models.TextField(default="")
    imageMetaDataJson = models.TextField(default="")
    roadNetworkJson = models.TextField(default="")
    mmsURL = models.TextField(default="")

    placeTypeHierarchy = models.TextField(default="[]")
    floorPlanImageUrl = models.ImageField(blank=True, null=True,
                                          upload_to=uploadFloorPlanPath)
    floorPlanImHeight = models.IntegerField(default="0")
    floorPlanImWidth = models.IntegerField(default="0")
    # calibrationJsonPath = models.f
    # created_date = models.DateTimeField(auto_now_add=True)
    # updated_date = models.DateTimeField(auto_now_add=True)
    # TODO see if i need this
    rtspURL = models.TextField(blank=True, null=False, default="")
    coordinates = models.TextField(default="[]")
    mapCoordinates = models.TextField(default="[]")

    mapZoom = models.FloatField(default="18.0")
    mapCenter = models.TextField(blank=True, null=True)
    originLat = models.FloatField(default="0.0")
    originLng = models.FloatField(default="0.0")
    cityPlace = models.TextField(blank=True, null=True)
    roomPlace = models.TextField(blank=True, null=True)
    def __str__(self):
        """A string representation of the model."""
        return self.name


class MMS(TimeStampedModel):
    id = models.AutoField(primary_key=True)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, default="")
    url = models.TextField(blank=False, null=False)

    def __str__(self):
        """A string representation of the model."""
        return self.url


class City(TimeStampedModel):
    id = models.AutoField(primary_key=True)
    sensorOnly = models.BooleanField(default=False)
    calibrationType = models.CharField(max_length=9,
                                       choices=CALIBRATION_CHOICES,
                                       default='geo')

    name = models.CharField(
        max_length=200, default="INITIAL_CITY", unique=True)
    mapAPIKey = models.CharField(max_length=200, default="", blank=True)
    # calibType = models.BooleanField(default=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, default="")
    mapFile = models.TextField(blank=True, null=False)
    mapZoom = models.FloatField(default="18.0")
    mapCenter = models.TextField(blank=True, null=True)
    originLat = models.FloatField(default="0.0")
    originLng = models.FloatField(default="0.0")
    mmsURL = models.TextField(default="")
    placeTypeHierarchy = models.TextField(default="[]")
    floorPlanImageUrl = models.ImageField(blank=True, null=True,
                                          upload_to=uploadFloorPlanPath)
    floorPlanImHeight = models.IntegerField(default="0")
    floorPlanImWidth = models.IntegerField(default="0")

    #TODO see if i need this
    rtspURL = models.TextField(blank=True, null=False, default="")
    coordinates = models.TextField(default="[]")
    mapCoordinates = models.TextField(default="[]")

    def __str__(self):
        """A string representation of the model."""
        return self.name


class PlaceTypes(TimeStampedModel):
    id = models.AutoField(primary_key=True)
    placeType = models.CharField(
        max_length=50, default="NEW_TYPE", unique=True)
    # city = models.ForeignKey(City, related_name="placeTypes_set",
    #                          on_delete=models.CASCADE)
    project = models.ForeignKey(
        Project, related_name="placeTypes_set",
                             on_delete=models.CASCADE)
    rank = models.IntegerField(default=0)

    def __str__(self):
        """A string representation of the model."""
        return self.placeType


class Place(TimeStampedModel):
    id = models.AutoField(primary_key=True)
    ### @TODO remove unique=True, and make sure name/value is unique pair
    name = models.CharField(
        max_length=200, default="NEW_PLACE", unique=True)
    # value = models.CharField(
    #     max_length=200, default="Value")
    originLat = models.FloatField(default="0.0")
    originLng = models.FloatField(default="0.0")
    placeType = models.ForeignKey(
        PlaceTypes, on_delete=models.CASCADE)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE)

    # class Meta:
    #     constraints = [
    #         models.UniqueConstraint(fields=["name","value"],
    #                                 name='name of constraint')
    #     ]

    def __str__(self):
        """A string representation of the model."""
        return self.name


class Intersection(TimeStampedModel):
    id = models.AutoField(primary_key=True)
    description = models.CharField(
        max_length=400, default="Intersection location.")
    numSensors = models.IntegerField(default=0)
    numCalibrated = models.IntegerField(default=0)
    lineSegments = models.TextField(max_length=None, default="[]")
    roadLinks = models.TextField(max_length=None, default="[]")
    linksAreDrawn = models.BooleanField(default=False)
    linksAreValid = models.BooleanField(default=False)
    mapZoom = models.FloatField(default="18.0")
    mapCenter = models.TextField(blank=True, null=True)
    originLat = models.FloatField(default="0.0")
    originLng = models.FloatField(default="0.0")
    majorRoad = models.CharField(max_length=200, default="ROAD_1")
    minorRoad = models.CharField(max_length=200, default="ROAD_2")
    name = models.CharField(
        max_length=200, default="ROAD_1_AND_ROAD_2")
    
    class Meta:
        unique_together = ('name', 'project')

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE)

    # city = models.ForeignKey(City,
    #                          on_delete=models.CASCADE)

    def __str__(self):
        """A string representation of the model."""
        return self.name


class Corridor(TimeStampedModel):
    id = models.AutoField(primary_key=True)
    name = models.CharField(
        max_length=200, default="NEW_CORRIDOR")
    class Meta:
        unique_together = ('name', 'project')
    mapZoom = models.FloatField(default="18.0")
    mapCenter = models.TextField(blank=True, null=True)
    originLat = models.FloatField(default="0.0")
    originLng = models.FloatField(default="0.0")
    corridorShape = models.TextField(max_length=None, default="[]")
    directions = models.TextField(max_length=100, default="[]")
    length = models.FloatField(default="0.0")

    project = models.ForeignKey(Project,
                                on_delete=models.CASCADE)

    def __str__(self):
        """A string representation of the model."""
        return self.name



DIRECTION_CHOICES = (('NW', 'NW'), ('NNW', 'NNW'), ('N', 'N'),
                     ('NNE', 'NNE'), ('NE', 'NE'),
                     ('ENE', 'ENE'), ('E', 'E'), ('ESE', 'ESE'),
                     ('SE', 'SE'), ('SSE', 'SSE'), ('S', 'S'),
                     ('SSW', 'SSW'), ('SW', 'SW'),
                     ('WSW', 'WSW'), ('W', 'W'), ('WNW', 'WNW'))


class Sensor(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sensorId = models.CharField(
        max_length=200, default="NEW_SENSOR")

    class Meta:
        unique_together = ('sensorId', 'project')
    # sensorOnly = models.BooleanField(default=False)
    # cartesianOnly = models.BooleanField(default=False)
    height = models.IntegerField(default="0")
    width = models.IntegerField(default="0")
    isCalibrated = models.BooleanField(default=False)
    isValidated = models.BooleanField(default=False)
    mapAPIKey = models.CharField(max_length=200, default="")
    calibrationType = models.CharField(max_length=9,
                                       choices=CALIBRATION_CHOICES,
                                       default='geo')
    coordinates = models.TextField(max_length=None, default="[]")
    geoLocation = models.TextField(max_length=None, default="[]")
    sensorPolygon = models.TextField(max_length=None, default="[]")
    edgeLengths = models.TextField(max_length=None, default="[]")
    floorPlanPolygon = models.TextField(max_length=None, default="[]")
    gisPolygon = models.TextField(max_length=None, default="[]")
    roiPolygon = models.TextField(max_length=None, default="[]")
    cropRoiPolygon = models.TextField(max_length=None, default="[]")

    floorPlanImHeight = models.IntegerField(default="0")
    floorPlanImWidth = models.IntegerField(default="0")
    imHomography = models.TextField(max_length=None, default="")
    homography = models.TextField(max_length=None, default="")
    mapZoom = models.FloatField(default="18.0")
    mapCenter = models.TextField(blank=True, null=True)
    originLat = models.FloatField(default="0.0")
    originLng = models.FloatField(default="0.0")
    scaleFactor = models.FloatField(default="1.0")
    scaleFactorPolygon = models.TextField(max_length=None, default="[]")
    scalePolygon = models.TextField(max_length=None, default="[]")

    sensorName = models.CharField(
        max_length=200, default="ROAD_1_AND_ROAD_2__NW")
    cardinalDirection = models.CharField(max_length=5,
                                         choices=DIRECTION_CHOICES,
                                         default="NW")
    imageUrl = models.ImageField(blank=True, null=True,
                                 upload_to=uploadPath)
    invertImageUrl = models.ImageField(blank=True, null=True,
                                       upload_to=uploadInvPath)
    floorPlanImageUrl = models.ImageField(blank=True, null=True,
                                          upload_to=uploadFloorPlanSensorPath)
    rtspURL = models.TextField(blank=True, null=False, default="")

    invertImXPad = models.IntegerField(default="0")
    invertImYPad = models.IntegerField(default="0")
    invertImWidth = models.IntegerField(default="0")
    invertImHeight = models.IntegerField(default="0")

    # should these just be in Intersection
    majorRoad = models.CharField(max_length=200, default="ROAD_1")
    minorRoad = models.CharField(max_length=200, default="ROAD_2")
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, default="")
    # city = models.ForeignKey(City,
    #                          on_delete=models.CASCADE, default="")
    intersection_set = models.ForeignKey(
        Intersection, on_delete=models.CASCADE, null=True, blank=True)
    corridor_set = models.ManyToManyField(
        Corridor, blank=True)
    place_set = models.ManyToManyField(
        Place,  blank=True)


    #tripwire
    tripwireLines = models.TextField(max_length=None, default="[]")
    tripDirLines = models.TextField(max_length=None, default="[]")

    # MMS info
    mmsInfo_protocol = models.TextField(max_length=None, default="")
    mmsInfo_host = models.TextField(max_length=None, default="")
    mmsInfo_type = models.TextField(max_length=None, default="")

    # sensor details
    fps = models.TextField(default="0.0")
    deviceId = models.TextField(max_length=None, default="")
    videoURL = models.TextField(max_length=None, default="")
    depth = models.TextField(default="0.0")
    fieldOfView = models.TextField(default="0.0")
    direction = models.TextField(default="0.0")

    view = models.TextField(default="0.0")
    type = models.TextField(max_length=None, default="camera")

    def __str__(self):
        """A string representation of the model."""
        return self.sensorId

    def getMMS(self):
        return self.project.mmsURL


class MMSSensor(TimeStampedModel):
    id = models.AutoField(primary_key=True)
    sensorId = models.CharField(
        max_length=200, default="NEW_SENSOR", unique=True)
    rtspURL = models.TextField(blank=True, null=True)
