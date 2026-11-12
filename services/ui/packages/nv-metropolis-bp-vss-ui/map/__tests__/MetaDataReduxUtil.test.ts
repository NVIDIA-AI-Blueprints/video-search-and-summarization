// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import MetaDataReduxUtil from '../lib-src/utils/MetaDataReduxUtil';
import { VideoElementDefaultConfig } from '../lib-src/utils/Utils';

describe('VideoElementDefaultConfig', () => {
  it('has expected default dimensions', () => {
    expect(VideoElementDefaultConfig.htmlTagOptions.height).toBe('16.875vw');
    expect(VideoElementDefaultConfig.htmlTagOptions.width).toBe('30vw');
  });
});

describe('MetaDataReduxUtil', () => {
  it('exports a valid store slice', () => {
    expect(MetaDataReduxUtil.slice).toBeDefined();
    expect(MetaDataReduxUtil.slice.name).toBe('metadata');
    expect(typeof MetaDataReduxUtil.slice.reducer).toBe('function');
  });

  it('has updateMetaData action', () => {
    expect(MetaDataReduxUtil.slice.actions.updateMetaData).toBeDefined();
    expect(typeof MetaDataReduxUtil.slice.actions.updateMetaData).toBe('function');
  });

  it('builds sensor and place maps from calibration payload', () => {
    const action = MetaDataReduxUtil.slice.actions.updateMetaData({
      city: 'Warehouse',
      calibrationType: 'cartesian',
      sensors: [
        {
          id: 'cam-1',
          place: [
            { name: 'city', value: 'Warehouse' },
            { name: 'building', value: 'Building-1' },
            { name: 'room', value: 'Room-1' },
          ],
          geoLocation: { lat: 1, lng: 2 },
          scaleFactor: 1,
          translationToGlobalCoordinates: { x: 0, y: 0 },
        },
      ],
    });
    const next = MetaDataReduxUtil.slice.reducer({}, action);
    expect(next.sensorsMap.get('cam-1').id).toBe('cam-1');
    expect(next.roomsMap.has('Room-1')).toBe(true);
    expect(next.buildingsMap.has('Building-1')).toBe(true);
    expect(next.cityConfig.name).toBe('Warehouse');
  });
});
