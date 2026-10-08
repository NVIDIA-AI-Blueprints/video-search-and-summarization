# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from django.test import TestCase

from projects.models import Project


class ProjectTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        Project.objects.create(
            name='USA')
        Project.objects.create(
            name='CANADA')

    def test_projec1_content(self):
        project = Project.objects.get(id=1)
        self.assertEquals(project.name, 'USA')

    def test_project2_content(self):
        project = Project.objects.get(id=2)
        self.assertEquals(project.name, 'CANADA')
