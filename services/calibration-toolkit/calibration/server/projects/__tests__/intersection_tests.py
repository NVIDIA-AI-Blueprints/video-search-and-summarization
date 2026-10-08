# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from django.test import TestCase

from projects.models import Intersection


class IntersectionTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        Intersection.objects.create(
            name='ROAD1_AND_ROAD2',
            description='a description here',
            city_id=2,
            project_id=1)

    def test_intersection_content(self):
        intersection = Intersection.objects.get(id=1)
        expected_int_name = f'{intersection.name}'
        expected_int_desc = f'{intersection.description}'
        expected_int_cityId = f'{intersection.city_id}'
        expected_int_projectId = f'{intersection.project_id}'
        self.assertEquals(expected_int_name, 'ROAD1_AND_ROAD2')
        self.assertEquals(expected_int_desc, 'a description here')
        self.assertEquals(expected_int_cityId, '2')
        self.assertEquals(expected_int_projectId, '1')
