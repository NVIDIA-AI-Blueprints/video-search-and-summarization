# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import copy
import json
from pathlib import Path

import pytest

from streaming.config import source_config


STREAM = "fe4ebbb9-5bb9-4f79-99b3-e9cbc45e48f9"
CALIBRATION = json.loads((Path(__file__).parents[2] / 'backend/neural_measurement_calibration.json').read_text())
ASSET = "061005c045b18b0f555cc1ad1ac7a037d80b3cf4c0fa0fb3b5ca3357bcba5818"


def profile():
    return dict(url="rtsp://camera/live", width=1280, height=720, fps=24,
                profile_id="reviewed-v3", reference_level=.71648, tolerance=.05,
                calibration_basis="Reviewed mask measurements", source_asset_sha256=ASSET,
                exterior_calibration=dict(engine="calibrated-exterior-color-signal-v1",
                    reviewed_source_sha256=ASSET, reference_size=[1280, 720],
                    boxes=[[659, 211, 18, 25]], orange_hsv_lower=[5,115,115],
                    orange_hsv_upper=[36,255,255], pixels_min=60, persistence_seconds=.25,
                    basis="Reviewed opaque exterior neck ring"))


def load(tmp_path, source):
    path=tmp_path/'streams.json'
    path.write_text(json.dumps(dict(schema_version=1, streams={STREAM:source})))
    return source_config(path, STREAM, CALIBRATION)


def test_old_source_retains_old_regions_and_new_source_uses_its_reviewed_regions(tmp_path):
    old=profile(); del old['exterior_calibration']
    a=load(tmp_path,old)
    b=load(tmp_path,profile())
    assert a['calibration']['exterior']==CALIBRATION['exterior']
    assert b['calibration']['exterior']['boxes']==[[659,211,18,25]]
    assert a['calibration_sha256']!=b['calibration_sha256']
    b['calibration']['exterior']['boxes'][0][0]=0
    assert CALIBRATION['exterior']['boxes'][0][0]==349
    assert a['calibration']['exterior']['boxes'][0][0]==349


@pytest.mark.parametrize(('key','value'),[
    ('reviewed_source_sha256','0'*64), ('engine','unknown'), ('basis',''),
    ('reference_size',[1280,0]), ('boxes',[[1270,10,18,25]]),
    ('boxes',[]), ('boxes',[[0,0,True,10]]), ('pixels_min',451),
    ('pixels_min',0), ('orange_hsv_upper',[180,255,255]),
    ('orange_hsv_lower',[37,115,115]), ('persistence_seconds',float('nan')),
])
def test_unreviewed_or_invalid_camera_regions_are_rejected(tmp_path,key,value):
    source=profile(); source['exterior_calibration'][key]=value
    with pytest.raises(ValueError): load(tmp_path,source)


def test_same_profile_different_reviewed_region_has_distinct_provenance(tmp_path):
    source=profile(); a=load(tmp_path,source)
    source['exterior_calibration']['boxes'][0][0]+=1
    b=load(tmp_path,source)
    assert a['calibration_sha256']!=b['calibration_sha256']
