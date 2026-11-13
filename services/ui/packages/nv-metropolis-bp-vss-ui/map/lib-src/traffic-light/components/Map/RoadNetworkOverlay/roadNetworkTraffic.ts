// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { SpeedToTrafficColor } from "../../../utils/Utils";

export type LatLngLiteral = { lat: number; lng: number; alt?: number };

export type RoadSegmentRecord = {
  id: string;
  direction?: string;
  start?: LatLngLiteral;
  end?: LatLngLiteral;
  points?: LatLngLiteral[];
  speed?: number | string | null;
  objectCount?: number | string | null;
};

export type RoadNetworkConfig = {
  city?: string;
  intersections?: Array<{
    name?: string;
    segments?: Array<{
      id?: string;
      direction?: string;
      start?: LatLngLiteral;
      end?: LatLngLiteral;
      points?: LatLngLiteral[];
    }>;
  }>;
};

export const toFiniteNumber = (value: unknown): number => {
  if (value === null || value === undefined || value === "") {
    return Number.NaN;
  }
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : Number.NaN;
};

/**
 * Original ITS: `(segment.objectCount || segment.speed)`.
 * 0 is inactive. A non-zero count or non-zero speed activates the link.
 */
export const segmentHasLiveTraffic = (segment?: RoadSegmentRecord | null): boolean => {
  const count = toFiniteNumber(segment?.objectCount);
  const speed = toFiniteNumber(segment?.speed);
  return (Number.isFinite(count) && count !== 0) || (Number.isFinite(speed) && speed !== 0);
};

/**
 * Original ITS color rules:
 * - inactive → gray
 * - active with no objectCount → free-flow green (speed 1000)
 * - active with a numeric speed → SpeedToTrafficColor(speed)
 */
export const segmentTrafficColor = (
  segment: RoadSegmentRecord | undefined,
  isActive: boolean
): string => {
  if (!isActive) {
    return SpeedToTrafficColor(-1);
  }
  const count = toFiniteNumber(segment?.objectCount);
  if (!(count > 0)) {
    return SpeedToTrafficColor(1000);
  }
  const speed = toFiniteNumber(segment?.speed);
  if (!Number.isFinite(speed)) {
    return SpeedToTrafficColor(-1);
  }
  return SpeedToTrafficColor(speed);
};

/**
 * Speed-box label. Original shows a number only when the link is active
 * and objectCount is truthy; otherwise the box is empty "mph".
 */
export const segmentSpeedLabel = (
  segment: RoadSegmentRecord | undefined,
  isActive: boolean
): string => {
  if (!isActive) {
    return "";
  }
  const count = toFiniteNumber(segment?.objectCount);
  if (!(count > 0)) {
    return "";
  }
  const speed = toFiniteNumber(segment?.speed);
  if (!Number.isFinite(speed)) {
    return "";
  }
  return Math.abs(speed).toString();
};

export const mergeRoadSegments = (
  previous: RoadSegmentRecord[] = [],
  incoming: RoadSegmentRecord[] = []
): RoadSegmentRecord[] => {
  const byId = new Map<string, RoadSegmentRecord>();
  for (const segment of previous) {
    if (segment?.id != null) {
      byId.set(segment.id, segment);
    }
  }
  if (!incoming.length) {
    return [...byId.values()];
  }
  for (const segment of incoming) {
    if (segment?.id == null) {
      continue;
    }
    const prev = byId.get(segment.id) || {};
    const nextPoints =
      Array.isArray(segment.points) && segment.points.length > 0
        ? segment.points
        : prev.points;
    byId.set(segment.id, {
      ...prev,
      ...segment,
      points: nextPoints,
    });
  }
  return [...byId.values()];
};

export const intersectionSegmentIdsFromRoadNetwork = (
  roadNetwork?: RoadNetworkConfig | null
): Map<string, Set<string>> => {
  const map = new Map<string, Set<string>>();
  for (const intersection of roadNetwork?.intersections || []) {
    if (!intersection?.name) {
      continue;
    }
    map.set(
      intersection.name,
      new Set(
        (intersection.segments || [])
          .map((segment) => segment?.id)
          .filter((id): id is string => Boolean(id))
      )
    );
  }
  return map;
};

export const seedSegmentsFromRoadNetwork = (
  roadNetwork?: RoadNetworkConfig | null
): RoadSegmentRecord[] => {
  const byId = new Map<string, RoadSegmentRecord>();
  for (const intersection of roadNetwork?.intersections || []) {
    for (const segment of intersection.segments || []) {
      if (!segment?.id || !Array.isArray(segment.points) || segment.points.length < 2) {
        continue;
      }
      byId.set(segment.id, {
        id: segment.id,
        direction: segment.direction,
        start: segment.start,
        end: segment.end,
        points: segment.points,
        speed: 0,
        objectCount: 0,
      });
    }
  }
  return [...byId.values()];
};

export const filterSegmentsForView = (
  roads: RoadSegmentRecord[] | undefined,
  viewType: string | undefined,
  selectedIntersection: string | undefined,
  intersectionSegmentIds?: Map<string, Set<string>>
): RoadSegmentRecord[] => {
  if (!roads) {
    return [];
  }
  if (viewType === "city-level-live") {
    return roads;
  }
  const ids = selectedIntersection
    ? intersectionSegmentIds?.get(selectedIntersection)
    : undefined;
  if (!ids || ids.size === 0) {
    return roads;
  }
  return roads.filter((segment) => ids.has(segment.id));
};

export const latLngLiteralFromMapEvent = (
  event?: { latLng?: { lat: number | (() => number); lng: number | (() => number) } | null }
): LatLngLiteral | null => {
  const latLng = event?.latLng;
  if (!latLng) {
    return null;
  }
  const lat = typeof latLng.lat === "function" ? latLng.lat() : latLng.lat;
  const lng = typeof latLng.lng === "function" ? latLng.lng() : latLng.lng;
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    return null;
  }
  return { lat, lng };
};
