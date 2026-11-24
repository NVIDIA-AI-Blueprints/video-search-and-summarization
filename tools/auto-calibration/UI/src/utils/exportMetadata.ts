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
 * Optional sensor / ROI fields merged into cartesian *_exported.json on download.
 */

export interface PlaceLevel {
    name: string;
    value: string;
  }
  
  /** Comma/semicolon-separated lists as typed; parsed only when merging into export JSON. */
  export interface RoiMetaEdit {
    id: string;
    restrictedObjectTypesInput: string;
    confinedObjectTypesInput: string;
    type: string;
  }

  export interface TripwireMetaEdit {
    id: string;
  }
  
  export interface SensorMetaEdit {
    depth: string;
    fieldOfView: string;
    direction: string;
    direction3d: string;
    place: PlaceLevel[];
    rois: RoiMetaEdit[];
    tripwires: TripwireMetaEdit[];
  }
  
  function getAttr(
    attributes: Array<{ name: string; value: string }> | undefined,
    name: string
  ): string {
    const a = attributes?.find((x) => x.name === name);
    return a?.value ?? '';
  }
  
  function setAttr(
    attributes: Array<{ name: string; value: string }>,
    name: string,
    value: string
  ): Array<{ name: string; value: string }> {
    const next = attributes.map((x) => ({ ...x }));
    const i = next.findIndex((x) => x.name === name);
    if (i >= 0) next[i] = { name, value };
    else next.push({ name, value });
    return next;
  }
  
  /** Parse comma / semicolon separated tokens; empty -> [] */
  export function parseObjectTypeList(s: string): string[] {
    return s
      .split(/[,;]/)
      .map((t) => t.trim())
      .filter(Boolean);
  }
  
  export function formatObjectTypeList(arr: string[]): string {
    return arr.join(', ');
  }
  
  export function extractSensorMetaEdits(doc: {
    sensors?: Array<{
      attributes?: Array<{ name: string; value: string }>;
      place?: PlaceLevel[];
      rois?: Array<{
        id?: string;
        restrictedObjectTypes?: string[];
        confinedObjectTypes?: string[];
        type?: string;
      }>;
      tripwires?: Array<{ id?: string }>;
    }>;
  }): Record<number, SensorMetaEdit> {
    const out: Record<number, SensorMetaEdit> = {};
    const sensors = doc.sensors ?? [];
    sensors.forEach((s, i) => {
      const attrs = s.attributes ?? [];
      const rois = s.rois ?? [];
      const tripwires = s.tripwires ?? [];
      out[i] = {
        depth: getAttr(attrs, 'depth'),
        fieldOfView: getAttr(attrs, 'fieldOfView'),
        direction: getAttr(attrs, 'direction'),
        direction3d: getAttr(attrs, 'direction3d'),
        place: Array.isArray(s.place)
          ? s.place.map((p) => ({ name: String(p.name ?? ''), value: String(p.value ?? '') }))
          : [],
        rois: rois.map((r) => ({
          id: r.id != null ? String(r.id) : '',
          restrictedObjectTypesInput: formatObjectTypeList(
            Array.isArray(r.restrictedObjectTypes) ? [...r.restrictedObjectTypes] : [],
          ),
          confinedObjectTypesInput: formatObjectTypeList(
            Array.isArray(r.confinedObjectTypes) ? [...r.confinedObjectTypes] : [],
          ),
          type: r.type != null ? String(r.type) : '',
        })),
        tripwires: tripwires.map((t) => ({
          id: t.id != null ? String(t.id) : '',
        })),
      };
    });
    return out;
  }
  
  export function mergeSensorMetaIntoExport(
    doc: Record<string, unknown>,
    edits: Record<number, SensorMetaEdit>
  ): Record<string, unknown> {
    const next = structuredClone(doc) as {
      sensors?: Array<{
        attributes?: Array<{ name: string; value: string }>;
        place?: PlaceLevel[];
        rois?: Array<Record<string, unknown>>;
        tripwires?: Array<Record<string, unknown>>;
      }>;
    };
    const sensors = next.sensors;
    if (!sensors) return next;
  
    sensors.forEach((sensor, i) => {
      const e = edits[i];
      if (!e) return;
  
      let attrs = [...(sensor.attributes ?? [])];
      attrs = setAttr(attrs, 'depth', e.depth);
      attrs = setAttr(attrs, 'fieldOfView', e.fieldOfView);
      attrs = setAttr(attrs, 'direction', e.direction);
      attrs = setAttr(attrs, 'direction3d', e.direction3d);
      sensor.attributes = attrs;
  
      sensor.place = e.place
        .map((p) => ({ name: String(p.name ?? '').trim(), value: String(p.value ?? '').trim() }))
        .filter((p) => p.name !== '' || p.value !== '');
  
      const rois = sensor.rois ?? [];
      e.rois.forEach((re, j) => {
        if (j >= rois.length) return;
        const r = rois[j] as Record<string, unknown>;
        const newRoiId = re.id.trim();
        if (newRoiId) r.id = newRoiId;
        r.restrictedObjectTypes = parseObjectTypeList(re.restrictedObjectTypesInput);
        r.confinedObjectTypes = parseObjectTypeList(re.confinedObjectTypesInput);
        if (re.type.trim()) r.type = re.type.trim();
        else delete r.type;
      });

      const tws = sensor.tripwires ?? [];
      e.tripwires.forEach((te, j) => {
        if (j >= tws.length) return;
        const t = tws[j] as Record<string, unknown>;
        const newTwId = te.id.trim();
        if (newTwId) t.id = newTwId;
        delete t.restrictedObjectTypes;
        delete t.confinedObjectTypes;
      });
    });
  
    return next as Record<string, unknown>;
  }

/** Root-level `rois[]` entries (global layout ROIs) — edited in Full Export dialog. */
export interface GlobalRootRoiMetaEdit {
  id: string;
  type: string;
  restrictedObjectTypesInput: string;
  confinedObjectTypesInput: string;
}

/** Root-level `tripwires[]` (global layout) — id only; no groups or object-type lists. */
export interface GlobalRootTripwireMetaEdit {
  id: string;
}

export function extractGlobalRootRoiMetaEdits(doc: {
  rois?: Array<{
    id?: string;
    type?: string;
    restrictedObjectTypes?: string[];
    confinedObjectTypes?: string[];
  }>;
}): Record<number, GlobalRootRoiMetaEdit> {
  const rois = doc.rois ?? [];
  const out: Record<number, GlobalRootRoiMetaEdit> = {};
  rois.forEach((r, i) => {
    out[i] = {
      id: r.id != null ? String(r.id) : '',
      type: r.type != null ? String(r.type) : '',
      restrictedObjectTypesInput: formatObjectTypeList(
        Array.isArray(r.restrictedObjectTypes) ? [...r.restrictedObjectTypes] : [],
      ),
      confinedObjectTypesInput: formatObjectTypeList(
        Array.isArray(r.confinedObjectTypes) ? [...r.confinedObjectTypes] : [],
      ),
    };
  });
  return out;
}

export function mergeGlobalRootRoiMetaIntoExport(
  doc: Record<string, unknown>,
  edits: Record<number, GlobalRootRoiMetaEdit>,
): Record<string, unknown> {
  const next = structuredClone(doc) as { rois?: Array<Record<string, unknown>> };
  const rois = next.rois;
  if (!Array.isArray(rois)) return next;

  Object.entries(edits).forEach(([key, e]) => {
    const j = Number(key);
    if (!Number.isInteger(j) || j < 0 || j >= rois.length) return;
    const r = rois[j];
    const oldId = String(r.id ?? '');
    const newId = e.id.trim();
    if (newId && newId !== oldId) {
      r.id = newId;
      if (oldId) {
        (next as { sensors?: Array<{ projectedGlobalRois?: Array<{ id?: string }> }> }).sensors?.forEach((s) => {
          for (const p of s.projectedGlobalRois ?? []) {
            if (String(p.id ?? '') === oldId) p.id = newId;
          }
        });
      }
    } else if (newId) {
      r.id = newId;
    }
    r.type = e.type.trim();
    r.restrictedObjectTypes = parseObjectTypeList(e.restrictedObjectTypesInput);
    r.confinedObjectTypes = parseObjectTypeList(e.confinedObjectTypesInput);
  });

  return next as Record<string, unknown>;
}

export function extractGlobalRootTripwireMetaEdits(doc: {
  tripwires?: Array<{
    id?: string;
  }>;
}): Record<number, GlobalRootTripwireMetaEdit> {
  const tws = doc.tripwires ?? [];
  const out: Record<number, GlobalRootTripwireMetaEdit> = {};
  tws.forEach((t, i) => {
    out[i] = {
      id: t.id != null ? String(t.id) : '',
    };
  });
  return out;
}

export function mergeGlobalRootTripwireMetaIntoExport(
  doc: Record<string, unknown>,
  edits: Record<number, GlobalRootTripwireMetaEdit>,
): Record<string, unknown> {
  const next = structuredClone(doc) as { tripwires?: Array<Record<string, unknown>> };
  const tws = next.tripwires;
  if (!Array.isArray(tws)) return next;

  Object.entries(edits).forEach(([key, e]) => {
    const j = Number(key);
    if (!Number.isInteger(j) || j < 0 || j >= tws.length) return;
    const t = tws[j];
    const oldId = String(t.id ?? '');
    const newId = e.id.trim();
    if (newId && newId !== oldId) {
      t.id = newId;
      if (oldId) {
        (next as { sensors?: Array<{ projectedGlobalTripwires?: Array<{ id?: string }> }> }).sensors?.forEach(
          (s) => {
            for (const p of s.projectedGlobalTripwires ?? []) {
              if (String(p.id ?? '') === oldId) p.id = newId;
            }
          },
        );
      }
    } else if (newId) {
      t.id = newId;
    }
    delete t.restrictedObjectTypes;
    delete t.confinedObjectTypes;
  });

  return next as Record<string, unknown>;
}
