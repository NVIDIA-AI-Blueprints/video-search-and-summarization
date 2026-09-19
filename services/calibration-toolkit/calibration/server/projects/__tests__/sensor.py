# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from django.test import TestCase

from projects.models import Sensor


class SensorTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        Sensor.objects.create(
            sensorId='Sensor1',
            sensorName='Sensor1',
            majorRoad='Road1',
            minorRoad='Road2',
            originLat=42,
            originLng=-90,
            cardinalDirection="ENE",
            project_id=5,
            city_id=2)
        Sensor.objects.create(
            sensorId='Sensor2',
            sensorName='Sensor2',
            majorRoad='RoadA',
            minorRoad='RoadB',
            originLat=42.4897,
            originLng=-90.677,
            cardinalDirection="NE",
            project_id=5,
            city_id=2)

    def test_sensor1_content(self):
        sensor = Sensor.objects.get(id=1)

        # Check assignments
        self.assertEquals(sensor.sensorId, "Sensor1")
        self.assertEquals(sensor.sensorName, "Sensor1")
        self.assertEquals(sensor.majorRoad, "Road1")
        self.assertEquals(sensor.minorRoad, "Road2")
        self.assertEquals(sensor.originLat, 42)
        self.assertEquals(sensor.originLng, -90)
        self.assertEquals(sensor.cardinalDirection, "ENE")
        self.assertEquals(sensor.project_id, 5)
        self.assertEquals(sensor.city_id, 2)
        # Check defaults
        self.assertEquals(sensor.isCalibrated, defaulIsCalib)
        self.assertEquals(sensor.isValidated, defaultIsValid)
        self.assertEquals(sensor.mapZoom, defaultZoom)
        self.assertEquals(sensor.mapCenter, defaultCenter)
        self.assertEquals(sensor.sensorPolygon, defaultPoly)
        self.assertEquals(sensor.gisPolygon, defaultPoly)
        self.assertEquals(sensor.roiPolygon, defaultPoly)
        self.assertEquals(sensor.homography, defaultHomography)
        self.assertEquals(sensor.rtspURL, defaultRtsp)

    def test_sensor2_content(self):
        sensor = Sensor.objects.get(id=2)

        # Check assignments
        self.assertEquals(sensor.sensorId, "Sensor2")
        self.assertEquals(sensor.sensorName, "Sensor2")
        self.assertEquals(sensor.majorRoad, "RoadA")
        self.assertEquals(sensor.minorRoad, "RoadB")
        self.assertEquals(sensor.originLat, 42.4897)
        self.assertEquals(sensor.originLng, -90.677)
        self.assertEquals(sensor.cardinalDirection, "NE")
        self.assertEquals(sensor.project_id, 5)
        self.assertEquals(sensor.city_id, 2)
        # Check defaults
        self.assertEquals(sensor.isCalibrated, defaulIsCalib)
        self.assertEquals(sensor.isValidated, defaultIsValid)
        self.assertEquals(sensor.mapZoom, defaultZoom)
        self.assertEquals(sensor.mapCenter, defaultCenter)
        self.assertEquals(sensor.sensorPolygon, defaultPoly)
        self.assertEquals(sensor.gisPolygon, defaultPoly)
        self.assertEquals(sensor.roiPolygon, defaultPoly)
        self.assertEquals(sensor.homography, defaultHomography)
        self.assertEquals(sensor.rtspURL, defaultRtsp)


# Default values for sensor
defaulIsCalib = False
defaultIsValid = False
defaultZoom = 18.0
defaultCenter = None
defaultPoly = "[]"
defaultHomography = ""
defaultRtsp = None
