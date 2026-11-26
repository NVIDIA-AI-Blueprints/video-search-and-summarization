// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import {
  animationStepMs,
  anomalyHasTrajectory,
  anomalyTimeWindow,
  behaviorIdsForAnomaly,
  mergeBehaviorLocations,
  prepareAnomalyForReplay,
  takeAnomalyList,
  toLatLngLiteral,
  trajectoryLatLngs,
} from '../../lib-src/traffic-light/utils/anomalyReplay';

describe('takeAnomalyList', () => {
  it('returns an empty list when the payload is missing', () => {
    expect(takeAnomalyList(undefined, 20)).toEqual([]);
    expect(takeAnomalyList(null, 20)).toEqual([]);
    expect(takeAnomalyList({}, 20)).toEqual([]);
  });

  it('caps the list at alertListLength', () => {
    expect(takeAnomalyList([1, 2, 3, 4], 2)).toEqual([1, 2]);
  });
});

describe('trajectory points', () => {
  it('maps GeoJSON [lng, lat] arrays', () => {
    expect(toLatLngLiteral([-121.9, 37.4])).toEqual({ lat: 37.4, lng: -121.9 });
    expect(trajectoryLatLngs([[-121.9, 37.4], [-121.91, 37.41]])).toEqual([
      { lat: 37.4, lng: -121.9 },
      { lat: 37.41, lng: -121.91 },
    ]);
  });

  it('keeps {lat, lng} literals', () => {
    expect(toLatLngLiteral({ lat: 37.4, lng: -121.9 })).toEqual({
      lat: 37.4,
      lng: -121.9,
    });
  });

  it('unwraps protobuf {point: [lng, lat]}', () => {
    expect(toLatLngLiteral({ point: [-121.9, 37.4] })).toEqual({
      lat: 37.4,
      lng: -121.9,
    });
    expect(
      trajectoryLatLngs({
        coordinates: [{ point: [-121.9, 37.4] }, { point: [-121.91, 37.41] }],
      }),
    ).toEqual([
      { lat: 37.4, lng: -121.9 },
      { lat: 37.41, lng: -121.91 },
    ]);
  });
});

describe('animationStepMs', () => {
  it('uses timeInterval when present', () => {
    expect(animationStepMs({ timeInterval: 10 }, 10)).toBe(1000);
  });

  it('falls back to timestamp/end, then 100ms', () => {
    expect(
      animationStepMs(
        {
          timestamp: '2026-11-13T00:00:00.000Z',
          end: '2026-11-13T00:00:05.000Z',
        },
        5,
      ),
    ).toBe(1000);
    expect(animationStepMs({}, 4)).toBe(100);
  });
});

describe('prepareAnomalyForReplay', () => {
  it('prefers smoothLocations and fills geoCoordinates', () => {
    const prepared = prepareAnomalyForReplay(
      {
        type: 'mdx-incidents',
        smoothLocations: { coordinates: [[-121.9, 37.4]] },
        locations: { coordinates: [[0, 0]] },
      },
      false,
    );
    expect(prepared.locations).toEqual({
      coordinates: [[-121.9, 37.4]],
      geoCoordinates: [{ lat: 37.4, lng: -121.9 }],
    });
  });

  it('does not throw when locations are missing', () => {
    const prepared = prepareAnomalyForReplay({ type: 'mdx-alerts' }, false);
    expect(prepared.locations).toEqual({
      geoCoordinates: [],
      coordinates: [],
    });
  });
});

describe('behavior location lookup', () => {
  it('builds sensor #-# object behavior ids', () => {
    expect(
      behaviorIdsForAnomaly({
        sensorId: 'Camera_02',
        objectIds: ['11'],
        info: { primaryObjectId: '11' },
        id: 'abc123hash',
      }),
    ).toEqual(['Camera_02 #-# 11']);
  });

  it('keeps explicit behavior ids', () => {
    expect(
      behaviorIdsForAnomaly({
        id: 'Cam1 #-# 7',
        behaviorId: 'Cam2 #-# 8',
      }),
    ).toEqual(['Cam2 #-# 8', 'Cam1 #-# 7']);
  });

  it('derives a time window from timestamp/end', () => {
    expect(
      anomalyTimeWindow({
        timestamp: '2026-11-13T00:00:00.000Z',
        end: '2026-11-13T00:00:05.000Z',
      }),
    ).toEqual({
      fromTimestamp: '2026-11-13T00:00:00.000Z',
      toTimestamp: '2026-11-13T00:00:05.000Z',
    });
  });

  it('merges the first behavior that has a trajectory', () => {
    const merged = mergeBehaviorLocations(
      { type: 'mdx-incidents', sensorId: 'Cam1' },
      [
        { id: 'empty' },
        {
          id: 'Cam1 #-# 11',
          timeInterval: 4,
          smoothLocations: { coordinates: [[-121.9, 37.4], [-121.91, 37.41]] },
        },
      ],
      false,
    );
    expect(anomalyHasTrajectory(merged)).toBe(true);
    expect(merged.timeInterval).toBe(4);
    expect(merged.locations.geoCoordinates).toEqual([
      { lat: 37.4, lng: -121.9 },
      { lat: 37.41, lng: -121.91 },
    ]);
  });
});
