# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from django.test import TestCase

from projects.models import City


class CityTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        City.objects.create(
            name='DUBUQUE',
            originLat=42.4897,
            originLng=-90.677,
            project_id=3)
        City.objects.create(
            name='SANTA_CLARA',
            originLat=37.352261,
            originLng=-121.957082,
            project_id=3)

    def test_city1_content(self):
        city = City.objects.get(id=1)

        self.assertEquals(city.name, 'DUBUQUE')
        self.assertEquals(city.originLat, 42.4897)
        self.assertEquals(city.originLng, -90.677)
        self.assertEquals(city.project_id, 3)

    def test_city2_content(self):
        city = City.objects.get(id=2)

        self.assertEquals(city.name, 'SANTA_CLARA')
        self.assertEquals(city.originLat, 37.352261)
        self.assertEquals(city.originLng, -121.957082)
        self.assertEquals(city.project_id, 3)
