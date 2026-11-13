// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { SpeedToTrafficColor } from '../../lib-src/traffic-light/utils/Utils';
import {
  filterSegmentsForView,
  intersectionSegmentIdsFromRoadNetwork,
  latLngLiteralFromMapEvent,
  mergeRoadSegments,
  seedSegmentsFromRoadNetwork,
  segmentHasLiveTraffic,
  segmentSpeedLabel,
  segmentTrafficColor,
} from '../../lib-src/traffic-light/components/Map/RoadNetworkOverlay/roadNetworkTraffic';

describe('live road-link traffic rules', () => {
  it('treats 0/0 as inactive, matching original (objectCount || speed)', () => {
    expect(segmentHasLiveTraffic({ id: 'a', speed: 0, objectCount: 0 })).toBe(false);
    expect(segmentHasLiveTraffic({ id: 'a', speed: null, objectCount: null })).toBe(false);
    expect(segmentHasLiveTraffic({ id: 'a' })).toBe(false);
  });

  it('activates on non-zero count or non-zero speed', () => {
    expect(segmentHasLiveTraffic({ id: 'a', speed: 8, objectCount: 0 })).toBe(true);
    expect(segmentHasLiveTraffic({ id: 'a', speed: 0, objectCount: 3 })).toBe(true);
    expect(segmentHasLiveTraffic({ id: 'a', speed: '12', objectCount: '0' })).toBe(true);
  });

  it('colors inactive links gray', () => {
    expect(segmentTrafficColor({ id: 'a', speed: 40, objectCount: 4 }, false)).toBe(
      SpeedToTrafficColor(-1)
    );
    expect(segmentTrafficColor({ id: 'a', speed: 40, objectCount: 4 }, false)).toBe('#A0A0A0');
  });

  it('uses free-flow green when active but objectCount is 0', () => {
    expect(segmentTrafficColor({ id: 'a', speed: 8, objectCount: 0 }, true)).toBe(
      SpeedToTrafficColor(1000)
    );
    expect(segmentTrafficColor({ id: 'a', speed: 8, objectCount: 0 }, true)).toBe('#3CB371');
  });

  it('colors by mph when active with objectCount', () => {
    expect(segmentTrafficColor({ id: 'a', speed: 5, objectCount: 2 }, true)).toBe('#E60000');
    expect(segmentTrafficColor({ id: 'a', speed: 15, objectCount: 2 }, true)).toBe('#F08080');
    expect(segmentTrafficColor({ id: 'a', speed: 25, objectCount: 2 }, true)).toBe('#FFA769');
    expect(segmentTrafficColor({ id: 'a', speed: 40, objectCount: 2 }, true)).toBe('#98FB98');
    expect(segmentTrafficColor({ id: 'a', speed: 50, objectCount: 2 }, true)).toBe('#3CB371');
  });

  it('does not treat missing speed as free-flow green', () => {
    expect(segmentTrafficColor({ id: 'a', objectCount: 2 }, true)).toBe('#A0A0A0');
  });

  it('speed-box label is empty without count, otherwise abs(speed)', () => {
    expect(segmentSpeedLabel({ id: 'a', speed: 8, objectCount: 0 }, true)).toBe('');
    expect(segmentSpeedLabel({ id: 'a', speed: -12, objectCount: 3 }, true)).toBe('12');
    expect(segmentSpeedLabel({ id: 'a', speed: 12, objectCount: 3 }, false)).toBe('');
  });

  it('merges slim speed updates onto geometry from the detailed payload', () => {
    const detailed = [
      {
        id: 'seg-1',
        points: [
          { lat: 1, lng: 2 },
          { lat: 3, lng: 4 },
        ],
        speed: 0,
        objectCount: 0,
      },
    ];
    const slim = [{ id: 'seg-1', speed: 18, objectCount: 4 }];
    const merged = mergeRoadSegments(detailed, slim);
    expect(merged).toHaveLength(1);
    expect(merged[0].points).toEqual(detailed[0].points);
    expect(merged[0].speed).toBe(18);
    expect(merged[0].objectCount).toBe(4);
  });

  it('keeps previous segments when the incoming list is empty', () => {
    const previous = [{ id: 'seg-1', speed: 10, objectCount: 1 }];
    expect(mergeRoadSegments(previous, [])).toEqual(previous);
  });

  it('filters intersection view by road-network segment ids', () => {
    const network = {
      intersections: [
        { name: 'Agnew_head_on', segments: [{ id: 'a' }, { id: 'b' }] },
        { name: 'Lafayette_Agnew', segments: [{ id: 'c' }] },
      ],
    };
    const ids = intersectionSegmentIdsFromRoadNetwork(network);
    const roads = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
    expect(
      filterSegmentsForView(roads, 'intersection-level-live', 'Agnew_head_on', ids).map(
        (s) => s.id
      )
    ).toEqual(['a', 'b']);
    expect(
      filterSegmentsForView(roads, 'city-level-live', 'Agnew_head_on', ids).map((s) => s.id)
    ).toEqual(['a', 'b', 'c']);
  });

  it('dedupes the same OSM segment id shared by two intersections', () => {
    const seeded = seedSegmentsFromRoadNetwork({
      intersections: [
        {
          name: 'A',
          segments: [
            {
              id: '5660102421-282408765',
              points: [
                { lat: 1, lng: 2 },
                { lat: 3, lng: 4 },
              ],
            },
          ],
        },
        {
          name: 'B',
          segments: [
            {
              id: '5660102421-282408765',
              points: [
                { lat: 5, lng: 6 },
                { lat: 7, lng: 8 },
              ],
            },
          ],
        },
      ],
    });
    expect(seeded.map((s) => s.id)).toEqual(['5660102421-282408765']);
    expect(
      mergeRoadSegments(
        [
          { id: '5660102421-282408765', speed: 0 },
          { id: '5660102421-282408765', speed: 1 },
        ],
        [],
      ),
    ).toHaveLength(1);
  });

  it('seeds inactive geometry from the road-network config', () => {
    const seeded = seedSegmentsFromRoadNetwork({
      intersections: [
        {
          name: 'Agnew_head_on',
          segments: [
            {
              id: 'a',
              points: [
                { lat: 1, lng: 2 },
                { lat: 3, lng: 4 },
              ],
            },
          ],
        },
      ],
    });
    expect(seeded).toEqual([
      {
        id: 'a',
        direction: undefined,
        start: undefined,
        end: undefined,
        points: [
          { lat: 1, lng: 2 },
          { lat: 3, lng: 4 },
        ],
        speed: 0,
        objectCount: 0,
      },
    ]);
  });

  it('reads OverlayView positions from LatLng or literals', () => {
    expect(
      latLngLiteralFromMapEvent({
        latLng: { lat: () => 37.1, lng: () => -121.9 },
      })
    ).toEqual({ lat: 37.1, lng: -121.9 });
    expect(latLngLiteralFromMapEvent({ latLng: { lat: 1, lng: 2 } })).toEqual({
      lat: 1,
      lng: 2,
    });
    expect(latLngLiteralFromMapEvent({})).toBeNull();
  });
});
