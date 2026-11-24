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

/** Refresh annotation geometry without restoring stale geometry from edited export JSON. */
type ExportDoc = Record<string, unknown>;
type ResultType = 'amc' | 'vggt';

function records(value: unknown): ExportDoc[] {
  return Array.isArray(value) ? value as ExportDoc[] : [];
}

function refreshFeatures(fresh: unknown, previous: unknown, geometryKeys: string[]): ExportDoc[] {
  const current = records(fresh);
  const old = records(previous);
  const used = new Set<ExportDoc>();
  const geometry = (item: ExportDoc) => JSON.stringify(geometryKeys.map(key => item[key]));
  // Reserve exact matches before assigning metadata for edited geometry.
  const matches = current.map(item => {
    const prior = old.find(candidate => !used.has(candidate) && geometry(candidate) === geometry(item));
    if (prior) used.add(prior);
    return prior;
  });
  return current.map((item, index) => {
    // Export IDs are sequential: after additions/deletions, match unchanged geometry
    // rather than assigning another annotation's metadata to the old array slot.
    const prior = matches[index] ?? (old.length === current.length
      ? old.find(candidate => !used.has(candidate)) : undefined);
    if (prior) used.add(prior);
    const merged = { ...item, ...prior };
    if (/^(roi|tripwire)-id-\d+$/.test(String(prior?.id))) merged.id = item.id;
    for (const key of geometryKeys) {
      if (key in item) merged[key] = item[key];
      else delete merged[key];
    }
    return merged;
  });
}

export function mergeFreshAnnotationGeometry(freshText: string, previousText?: string): string {
  const fresh = JSON.parse(freshText) as ExportDoc;
  if (!previousText) return freshText;
  const previous = JSON.parse(previousText) as ExportDoc;
  const oldSensors = records(previous.sensors);
  const merged = {
    ...fresh,
    ...previous,
    sensors: records(fresh.sensors).map((sensor, index) => {
      const old = oldSensors.find(candidate => candidate.id === sensor.id) ?? oldSensors[index];
      const next = {
        ...sensor,
        ...old,
        rois: refreshFeatures(sensor.rois, old?.rois, ['roiCoordinates']),
        tripwires: refreshFeatures(sensor.tripwires, old?.tripwires, ['wire', 'direction']),
      } as ExportDoc;
      // Projection snapshots belong to verification, not the final export.
      delete next.projectedGlobalRois;
      delete next.projectedGlobalTripwires;
      return next;
    }),
    rois: refreshFeatures(fresh.rois, previous.rois, ['roiCoordinates']),
    tripwires: refreshFeatures(fresh.tripwires, previous.tripwires, ['wire', 'direction']),
  };
  return JSON.stringify(merged, null, 2);
}

interface ExportApi {
  download: (type: ResultType) => Promise<string>;
  regenerate: (type: ResultType) => Promise<unknown>;
  save: (type: ResultType, text: string) => Promise<unknown>;
  backup?: {
    load: (type: ResultType) => string | undefined;
    store: (type: ResultType, text: string) => void;
    clear: (type: ResultType) => void;
  };
}

/** One session per project; serialize saves and refresh each pipeline after saved changes. */
export class AnnotationExportSession {
  private saveTail: Promise<unknown> = Promise.resolve();
  private saveErrors = new Map<string, unknown>();
  private revision = 0;
  private refreshed = new Map<ResultType, number>();
  private refreshes = new Map<ResultType, Promise<string>>();
  private preserved = new Map<ResultType, string>();

  constructor(private api: ExportApi) {}

  saveAnnotations(key: string, save: () => Promise<unknown>): Promise<void> {
    const task = this.saveTail.catch(() => undefined).then(async () => {
      try {
        await save();
        this.saveErrors.delete(key);
        this.revision += 1;
      } catch (error) {
        this.saveErrors.set(key, error);
        throw error;
      }
    });
    // Keep the queue usable after failure; callers still receive the rejected task.
    this.saveTail = task.catch(() => undefined);
    return task;
  }

  async waitForSaves(): Promise<void> {
    let tail: Promise<unknown>;
    do {
      tail = this.saveTail;
      await tail;
    } while (tail !== this.saveTail);
    if (this.saveErrors.size) {
      throw new Error('Annotation save failed. Save the affected camera or layout again before exporting.');
    }
  }

  getExport(type: ResultType): Promise<string> {
    const running = this.refreshes.get(type);
    if (running) return running;
    const task = this.refreshExport(type);
    this.refreshes.set(type, task);
    void task.then(
      () => this.refreshes.delete(type),
      () => this.refreshes.delete(type),
    );
    return task;
  }

  private async refreshExport(type: ResultType): Promise<string> {
    await this.waitForSaves();
    let previous: string | undefined;
    try {
      previous = this.preserved.get(type) ?? this.api.backup?.load(type) ?? await this.api.download(type);
    } catch (error) {
      if ((error as { status?: number }).status !== 404) throw error;
    }
    await this.waitForSaves();
    if (previous !== undefined) {
      this.preserved.set(type, previous);
      // Persist before regeneration overwrites the server file. Storage failure
      // aborts the refresh rather than risking loss of manually edited metadata.
      this.api.backup?.store(type, previous);
    }
    // First access also refreshes exports left stale by an earlier UI session.
    while (previous === undefined || this.refreshed.get(type) !== this.revision) {
      await this.waitForSaves();
      const revision = this.revision;
      await this.api.regenerate(type);
      const fresh = await this.api.download(type);
      await this.waitForSaves();
      if (revision !== this.revision) continue;
      const text = mergeFreshAnnotationGeometry(fresh, previous);
      // Preserve edited metadata on disk, not only in the downloaded browser blob.
      if (previous !== undefined) await this.api.save(type, text);
      await this.waitForSaves();
      if (revision !== this.revision) continue;
      this.refreshed.set(type, revision);
      this.preserved.delete(type);
      this.api.backup?.clear(type);
      return text;
    }
    this.preserved.delete(type);
    this.api.backup?.clear(type);
    return previous;
  }
}
