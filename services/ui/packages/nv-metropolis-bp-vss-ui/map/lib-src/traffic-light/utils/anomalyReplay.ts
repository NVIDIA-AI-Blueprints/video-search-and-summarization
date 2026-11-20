// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

export type LatLngLiteral = { lat: number; lng: number };

const toFiniteNumber = (value: unknown): number => {
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : Number.NaN;
};

export const takeAnomalyList = (data: unknown, maxItems: unknown): unknown[] => {
  const items = Array.isArray(data) ? data : [];
  const limit = toFiniteNumber(maxItems);
  if (!Number.isFinite(limit) || limit < 0) return items;
  return items.slice(0, limit);
};

export const extractCoordinateList = (source: unknown): unknown[] => {
  if (Array.isArray(source)) return source;
  if (!source || typeof source !== 'object') return [];
  const rec = source as { coordinates?: unknown; geoCoordinates?: unknown };
  if (Array.isArray(rec.coordinates) && rec.coordinates.length > 0) {
    return rec.coordinates;
  }
  if (Array.isArray(rec.geoCoordinates) && rec.geoCoordinates.length > 0) {
    return rec.geoCoordinates;
  }
  if (Array.isArray(rec.coordinates)) return rec.coordinates;
  return [];
};

export const toLatLngLiteral = (point: unknown): LatLngLiteral | null => {
  if (point == null) return null;
  if (Array.isArray(point) && point.length >= 2) {
    const lng = toFiniteNumber(point[0]);
    const lat = toFiniteNumber(point[1]);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
    return { lat, lng };
  }
  if (typeof point === 'object') {
    const rec = point as {
      lat?: unknown;
      lng?: unknown;
      lon?: unknown;
      point?: unknown;
    };
    if (rec.point != null) return toLatLngLiteral(rec.point);
    const lat = toFiniteNumber(rec.lat);
    const lng = toFiniteNumber(rec.lng ?? rec.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
    return { lat, lng };
  }
  return null;
};

export const trajectoryLatLngs = (coordinates: unknown): LatLngLiteral[] => {
  return extractCoordinateList(coordinates)
    .map(toLatLngLiteral)
    .filter((p): p is LatLngLiteral => p != null);
};

export const animationStepMs = (
  anomaly: { timeInterval?: unknown; timestamp?: unknown; end?: unknown } | null | undefined,
  pointCount: number,
): number => {
  if (!(pointCount > 0)) return 0;
  const intervalSec = toFiniteNumber(anomaly?.timeInterval);
  if (intervalSec > 0) return (intervalSec * 1000) / pointCount;
  const start = Date.parse(String(anomaly?.timestamp ?? ''));
  const end = Date.parse(String(anomaly?.end ?? ''));
  if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
    return (end - start) / pointCount;
  }
  return 100;
};

/**
 * Original ITS used smoothLocations unless debugAnomaly was on, then filled
 * geoCoordinates from [lng, lat] arrays so the map overlay can plot.
 */
const pickLocationSource = (
  alertData: Record<string, unknown>,
  debugAnomaly: boolean,
): unknown => {
  const info =
    alertData.info && typeof alertData.info === 'object'
      ? (alertData.info as Record<string, unknown>)
      : {};
  const behavior =
    alertData.behavior && typeof alertData.behavior === 'object'
      ? (alertData.behavior as Record<string, unknown>)
      : {};
  if (debugAnomaly) {
    return (
      alertData.locations ||
      info.locations ||
      behavior.locations ||
      alertData.smoothLocations
    );
  }
  return (
    alertData.smoothLocations ||
    info.smoothLocations ||
    behavior.smoothLocations ||
    alertData.locations ||
    info.locations ||
    behavior.locations
  );
};

export const prepareAnomalyForReplay = (
  alertData: Record<string, unknown> | null | undefined,
  debugAnomaly = false,
): Record<string, unknown> => {
  const next = { ...(alertData || {}) };
  const preferred = pickLocationSource(next, debugAnomaly);
  const coordinates = extractCoordinateList(preferred);
  const geoCoordinates = trajectoryLatLngs(coordinates);
  next.locations = {
    ...(preferred && !Array.isArray(preferred) && typeof preferred === 'object'
      ? (preferred as Record<string, unknown>)
      : {}),
    coordinates,
    geoCoordinates,
  };
  return next;
};

export const anomalyHasTrajectory = (anomaly: unknown): boolean => {
  if (!anomaly || typeof anomaly !== 'object') return false;
  const rec = anomaly as Record<string, unknown>;
  if (trajectoryLatLngs(rec.locations).length > 0) return true;
  return trajectoryLatLngs(pickLocationSource(rec, false)).length > 0;
};

const pushId = (ids: Set<string>, value: unknown) => {
  if (typeof value !== 'string') return;
  const trimmed = value.trim();
  if (trimmed) ids.add(trimmed);
};

export const behaviorIdsForAnomaly = (anomaly: unknown): string[] => {
  if (!anomaly || typeof anomaly !== 'object') return [];
  const rec = anomaly as Record<string, unknown>;
  const info =
    rec.info && typeof rec.info === 'object'
      ? (rec.info as Record<string, unknown>)
      : {};
  const behavior =
    rec.behavior && typeof rec.behavior === 'object'
      ? (rec.behavior as Record<string, unknown>)
      : {};
  const sensor =
    rec.sensor && typeof rec.sensor === 'object'
      ? (rec.sensor as Record<string, unknown>)
      : {};
  const behaviorSensor =
    behavior.sensor && typeof behavior.sensor === 'object'
      ? (behavior.sensor as Record<string, unknown>)
      : {};
  const object =
    rec.object && typeof rec.object === 'object'
      ? (rec.object as Record<string, unknown>)
      : {};

  const ids = new Set<string>();
  if (Array.isArray(rec.behaviorIds)) {
    rec.behaviorIds.forEach((id) => pushId(ids, id));
  }
  pushId(ids, rec.behaviorId);
  pushId(ids, behavior.id);
  if (typeof rec.id === 'string' && rec.id.includes('#-#')) {
    pushId(ids, rec.id);
  }

  const sensorId = rec.sensorId || sensor.id || behaviorSensor.id;
  const objectIds: unknown[] = [];
  if (Array.isArray(rec.objectIds)) objectIds.push(...rec.objectIds);
  if (rec.objectId != null) objectIds.push(rec.objectId);
  if (object.id != null) objectIds.push(object.id);
  if (info.primaryObjectId != null) objectIds.push(info.primaryObjectId);
  if (typeof sensorId === 'string' && sensorId) {
    objectIds.forEach((oid) => {
      if (oid == null || oid === '') return;
      pushId(ids, `${sensorId} #-# ${oid}`);
    });
  }
  return [...ids];
};

export const anomalyTimeWindow = (
  anomaly: unknown,
): { fromTimestamp: string; toTimestamp: string } | null => {
  if (!anomaly || typeof anomaly !== 'object') return null;
  const rec = anomaly as Record<string, unknown>;
  const start = Date.parse(String(rec.timestamp ?? ''));
  const end = Date.parse(String(rec.end ?? ''));
  if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
    return {
      fromTimestamp: new Date(start).toISOString(),
      toTimestamp: new Date(end).toISOString(),
    };
  }
  if (Number.isFinite(start)) {
    return {
      fromTimestamp: new Date(start).toISOString(),
      toTimestamp: new Date(start + 1000).toISOString(),
    };
  }
  return null;
};

export const mergeBehaviorLocations = (
  anomaly: Record<string, unknown> | null | undefined,
  behaviors: unknown,
  debugAnomaly = false,
): Record<string, unknown> => {
  const list = Array.isArray(behaviors) ? behaviors : [];
  const best =
    list.find(
      (item) =>
        item &&
        typeof item === 'object' &&
        anomalyHasTrajectory(item as Record<string, unknown>),
    ) || list[0];
  if (!best || typeof best !== 'object') {
    return prepareAnomalyForReplay(anomaly, debugAnomaly);
  }
  const behavior = best as Record<string, unknown>;
  return prepareAnomalyForReplay(
    {
      ...(anomaly || {}),
      timeInterval: anomaly?.timeInterval ?? behavior.timeInterval,
      timestamp: anomaly?.timestamp ?? behavior.timestamp,
      end: anomaly?.end ?? behavior.end,
      smoothLocations: behavior.smoothLocations ?? anomaly?.smoothLocations,
      locations: behavior.locations ?? anomaly?.locations,
    },
    debugAnomaly,
  );
};
