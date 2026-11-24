/*
* SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
* SPDX-License-Identifier: Apache-2.0
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
* http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*/

/**
 * Patch root-level global ROI / tripwire `sensors` lists in cartesian export JSON.
 * Projected geometry on each sensor is left unchanged.
 */

export interface CalibrationExportDoc {
  sensors?: Array<{
    id: string;
    projectedGlobalRois?: Array<{ id: string }>;
    projectedGlobalTripwires?: Array<{ id: string }>;
  }>;
  rois?: Array<{ id: string; sensors?: string[] }>;
  tripwires?: Array<{ id: string; sensors?: string[] }>;
}

/**
 * Match backend ``strip_video_filename_extension``: drop final ``.suffix`` on basename only.
 */
export function stripExportSensorId(name: string): string {
  const base = name.trim().split(/[/\\]/).pop() ?? name.trim();
  const i = base.lastIndexOf('.');
  if (i <= 0) return base;
  return base.slice(0, i);
}

/**
 * Resolve the sensor ``id`` used in cartesian export JSON for the given project video file.
 * Must match ``CalibrationExporter`` / ``sensor_id_for_export`` (map display name, else internal stem; never raw ``*.mp4``).
 */
export function sensorDisplayIdFromVideoFile(
  videoFile: string,
  cameraNameMap: Record<string, string>
): string {
  const stem = stripExportSensorId(videoFile);
  const mapped = cameraNameMap[stem];
  const raw =
    mapped !== undefined && mapped !== null && String(mapped).trim() !== ''
      ? String(mapped).trim()
      : stem;
  return stripExportSensorId(raw);
}

export function isSensorAssignedToGlobal(
  doc: CalibrationExportDoc,
  section: 'rois' | 'tripwires',
  globalId: string,
  sensorDisplayId: string
): boolean {
  const item = doc[section]?.find((x) => x.id === globalId);
  return Array.isArray(item?.sensors) && item!.sensors!.includes(sensorDisplayId);
}

export function toggleGlobalFeatureSensorAssignment(
  doc: CalibrationExportDoc,
  section: 'rois' | 'tripwires',
  globalId: string,
  sensorDisplayId: string,
  include: boolean
): CalibrationExportDoc {
  const next = structuredClone(doc) as CalibrationExportDoc;
  const list = next[section];
  if (!list) return next;
  const item = list.find((x) => x.id === globalId);
  if (!item) return next;
  const cur = new Set(item.sensors ?? []);
  if (include) cur.add(sensorDisplayId);
  else cur.delete(sensorDisplayId);
  item.sensors = Array.from(cur);
  return next;
}

export function stringifyExportDoc(doc: CalibrationExportDoc): string {
  return JSON.stringify(doc, null, 2);
}
