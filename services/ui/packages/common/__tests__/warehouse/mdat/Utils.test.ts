// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import {
  ParseMdxPlaceString,
  MdxPlaceArray2String,
  BeautifyNameString,
  MsToHMSsssString,
  GetSensorAttr,
} from '../../../lib-src/warehouse/mdat/utils/Utils';

describe('ParseMdxPlaceString', () => {
  it('parses a place string into key-value pairs', () => {
    const result = ParseMdxPlaceString('city=SanFrancisco/building=HQ/room=Lobby');
    expect(result).toEqual({
      city: 'SanFrancisco',
      building: 'HQ',
      room: 'Lobby',
    });
  });

  it('parses a single-level place string', () => {
    const result = ParseMdxPlaceString('city=Tokyo');
    expect(result).toEqual({ city: 'Tokyo' });
  });

  it('throws TypeError for non-string input', () => {
    expect(() => ParseMdxPlaceString(123)).toThrow(TypeError);
    expect(() => ParseMdxPlaceString(null)).toThrow(TypeError);
  });
});

describe('MdxPlaceArray2String', () => {
  const placeArray = [
    { name: 'city', value: 'SanFrancisco' },
    { name: 'building', value: 'HQ' },
    { name: 'room', value: 'Lobby' },
  ];

  it('converts place array to string', () => {
    expect(MdxPlaceArray2String(placeArray)).toBe(
      'city=SanFrancisco/building=HQ/room=Lobby',
    );
  });

  it('cuts off at specified place level', () => {
    expect(MdxPlaceArray2String(placeArray, 'building')).toBe(
      'city=SanFrancisco/building=HQ',
    );
  });

  it('returns undefined for null/undefined input', () => {
    expect(MdxPlaceArray2String(null)).toBeUndefined();
    expect(MdxPlaceArray2String(undefined)).toBeUndefined();
  });

  it('returns undefined when cutoff level not found', () => {
    const spy = jest.spyOn(console, 'error').mockImplementation();
    expect(MdxPlaceArray2String(placeArray, 'floor')).toBeUndefined();
    spy.mockRestore();
  });
});

describe('BeautifyNameString', () => {
  it('replaces underscores with spaces', () => {
    expect(BeautifyNameString('hello_world')).toBe('hello world');
  });

  it('handles string without underscores', () => {
    expect(BeautifyNameString('hello')).toBe('hello');
  });

  it('returns undefined for falsy input', () => {
    expect(BeautifyNameString(null)).toBeNull();
    expect(BeautifyNameString(undefined)).toBeUndefined();
  });
});

describe('MsToHMSsssString', () => {
  it('formats 0 ms', () => {
    expect(MsToHMSsssString(0)).toBe('00:00:00.000');
  });

  it('formats 1 hour 2 minutes 3 seconds 456 ms', () => {
    const ms = 1 * 3600000 + 2 * 60000 + 3 * 1000 + 456;
    expect(MsToHMSsssString(ms)).toBe('01:02:03.456');
  });

  it('formats seconds only', () => {
    expect(MsToHMSsssString(5000)).toBe('00:00:05.000');
  });
});

describe('getCartesianMapVizCanvasThrottleMsFromFps', () => {
  let getCartesianMapVizCanvasThrottleMsFromFps, getCartesianMapVizCanvasThrottleMsFromAppConfig;

  beforeAll(async () => {
    const mod = await import('../../../lib-src/warehouse/mdat/utils/cartesianMapVizCanvasThrottle');
    getCartesianMapVizCanvasThrottleMsFromFps = mod.getCartesianMapVizCanvasThrottleMsFromFps;
    getCartesianMapVizCanvasThrottleMsFromAppConfig = mod.getCartesianMapVizCanvasThrottleMsFromAppConfig;
  });

  it('converts fps to milliseconds', () => {
    expect(getCartesianMapVizCanvasThrottleMsFromFps(10)).toBe(100);
    expect(getCartesianMapVizCanvasThrottleMsFromFps(2)).toBe(500);
    expect(getCartesianMapVizCanvasThrottleMsFromFps(1)).toBe(1000);
  });

  it('uses default fps when null/undefined', () => {
    expect(getCartesianMapVizCanvasThrottleMsFromFps(null)).toBe(500);
    expect(getCartesianMapVizCanvasThrottleMsFromFps(undefined)).toBe(500);
  });

  it('returns fallback for zero or negative fps', () => {
    expect(getCartesianMapVizCanvasThrottleMsFromFps(0)).toBe(500);
    expect(getCartesianMapVizCanvasThrottleMsFromFps(-1)).toBe(500);
  });

  it('reads fps from appConfig', () => {
    expect(getCartesianMapVizCanvasThrottleMsFromAppConfig({ cartesianMapVizContentsRenderUpdateFps: 10 })).toBe(100);
    expect(getCartesianMapVizCanvasThrottleMsFromAppConfig(null)).toBe(500);
    expect(getCartesianMapVizCanvasThrottleMsFromAppConfig({})).toBe(500);
  });
});

describe('GetSensorAttr', () => {
  const sensorConfig = {
    id: 'sensor-1',
    attributes: [
      { name: 'resolution', value: '1080p' },
      { name: 'fps', value: '30' },
    ],
  };

  it('returns attribute value by name', () => {
    expect(GetSensorAttr(sensorConfig, 'resolution')).toBe('1080p');
    expect(GetSensorAttr(sensorConfig, 'fps')).toBe('30');
  });

  it('returns undefined for non-existent attribute', () => {
    expect(GetSensorAttr(sensorConfig, 'codec')).toBeUndefined();
  });

  it('returns undefined for null config', () => {
    expect(GetSensorAttr(null, 'resolution')).toBeUndefined();
  });

  it('returns undefined when attributes array is missing', () => {
    const spy = jest.spyOn(console, 'warn').mockImplementation();
    expect(GetSensorAttr({ id: 'x' }, 'resolution')).toBeUndefined();
    spy.mockRestore();
  });
});
