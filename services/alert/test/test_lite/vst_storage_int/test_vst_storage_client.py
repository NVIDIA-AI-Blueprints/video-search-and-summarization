#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import json
import types
import unittest
from unittest.mock import patch, MagicMock

from vst.its_vst_handler import ITS_VST_HANDLER


class TestVSTStorageClient(unittest.TestCase):
    def setUp(self):
        self.base_config = {
            'vst_config': {
                'base_url': 'http://vst-base',
                'request_timeout': 2,
                'storage': {
                    'base_url': 'http://vst-storage',
                    'media_file_path_by_id_endpoint': '/api/v1/storage/file/path'
                }
            },
            'ALERT_REVIEW_MEDIA_BASE_DIR': '/mnt/media'
        }
        self.handler = ITS_VST_HANDLER(self.base_config)

    @patch('vst.its_vst_handler.requests.get')
    def test_happy_path_resolves_absolute(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.content = b'{"mediaFilePath":"clip.mp4"}'
        mock_resp.json.return_value = { 'mediaFilePath': 'clip.mp4' }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        path = self.handler.get_media_file_path_by_vst_id('abc')
        self.assertEqual(path, '/mnt/media/clip.mp4')

    @patch('vst.its_vst_handler.requests.get')
    def test_missing_media_file_path_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.content = b'{}'
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        path = self.handler.get_media_file_path_by_vst_id('abc')
        self.assertIsNone(path)

    @patch('vst.its_vst_handler.requests.get')
    def test_http_error_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception('HTTP 500')
        mock_get.return_value = mock_resp

        path = self.handler.get_media_file_path_by_vst_id('abc')
        self.assertIsNone(path)

    @patch('vst.its_vst_handler.requests.get')
    def test_invalid_json_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.content = b'invalid'
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = ValueError('invalid json')
        mock_get.return_value = mock_resp

        path = self.handler.get_media_file_path_by_vst_id('abc')
        self.assertIsNone(path)

    @patch('vst.its_vst_handler.requests.get')
    def test_no_base_dir_returns_raw(self, mock_get):
        cfg = dict(self.base_config)
        cfg.pop('ALERT_REVIEW_MEDIA_BASE_DIR', None)
        handler = ITS_VST_HANDLER(cfg)

        mock_resp = MagicMock()
        mock_resp.content = b'{"mediaFilePath":"clip.mp4"}'
        mock_resp.json.return_value = { 'mediaFilePath': 'clip.mp4' }
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        path = handler.get_media_file_path_by_vst_id('abc')
        self.assertEqual(path, 'clip.mp4')


if __name__ == '__main__':
    unittest.main()


