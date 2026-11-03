// SPDX-License-Identifier: Apache-2.0

// Server-side validation of a structured (Class A) reviewer edit, built from
// the incident-contract-v3 schema itself (no second schema): the editable
// incident properties plus the contract's own entities / instruments / assets
// item schemas, then the contract's ID and holder rules and the window order.
// Class B (model-generated) and Class C (provenance) fields are rejected.
// Each item also names the item it came from in the revision the reviewer
// opened (source_id, required; null for an item the reviewer added) and its
// evidence image (image: an R2 key or null), which the save checks.

import Ajv2020 from 'ajv/dist/2020.js';
import type { ValidateFunction } from 'ajv';

import type { ContractCatalog } from '@/modules/contract/domain/ContractCatalog';
import { getContractCatalog } from '@/modules/contract/domain/ContractCatalog';
import type { IncidentContractReport } from '@/modules/contract/domain/types';
import { crossFieldViolations, type ContractViolation } from '@/modules/contract/domain/validate';
import type { StructuredEdit } from '@/modules/reports/domain/edit-draft';

export const EDITABLE_INCIDENT_FIELDS = ['type', 'start_timestamp', 'end_timestamp', 'description', 'severity_level'] as const;
export const EDITABLE_ENTITY_FIELDS = [
  'entity_id',
  'type',
  'description',
  'timestamp_seconds',
  'first_seen_seconds',
  'last_seen_seconds',
  'label',
] as const;
export const EDITABLE_INSTRUMENT_FIELDS = [
  'instrument_id',
  'entity_id',
  'name',
  'description',
  'threat_level',
  'timestamp_seconds',
  'label',
] as const;
export const EDITABLE_ASSET_FIELDS = ['asset_id', 'name', 'description', 'timestamp_seconds', 'label'] as const;
const EDIT_COLLECTIONS = ['incident', 'entities', 'instruments', 'assets'] as const;
// Not contract fields: the opened revision's item ID as stored (up to evidence_crops' 20 characters),
// and the item's evidence image key (up to the column's 1024 characters).
const SOURCE_ID_SCHEMA = { type: ['string', 'null'], minLength: 1, maxLength: 20 };
const IMAGE_SCHEMA = { type: ['string', 'null'], minLength: 1, maxLength: 1024 };
// Optional: where a reviewer-drawn image was cut from (the video second and the [ymin, xmin, ymax, xmax] box, 0-1000).
const IMAGE_TIMESTAMP_SCHEMA = { type: ['integer', 'null'], minimum: 0 };
const IMAGE_BOX_SCHEMA = { type: ['array', 'null'], items: { type: 'number', minimum: 0, maximum: 1000 }, minItems: 4, maxItems: 4 };

// A reviewer item from the box tool: the AI proposal kept for audit, the track (null for a still image) and, for a still image,
// its second and box. The server cuts the evidence image from the first-seen second and stores the track; none of it is trusted as a key.
const BOX_SCHEMA = { type: 'array', items: { type: 'number', minimum: 0, maximum: 1000 }, minItems: 4, maxItems: 4 };
const REVIEWER_SCHEMA = {
  type: ['object', 'null'],
  additionalProperties: false,
  required: ['ai_proposal', 'track', 'still_seconds', 'still_box'],
  properties: {
    ai_proposal: { type: ['object', 'null'] },
    track: {
      type: ['object', 'null'],
      additionalProperties: false,
      required: ['source', 'samples', 'span', 'partial', 'lost_at'],
      properties: {
        source: { const: 'reviewer' },
        samples: {
          type: 'array',
          minItems: 1,
          maxItems: 2000,
          items: {
            type: 'object',
            additionalProperties: false,
            required: ['t', 'box'],
            properties: { t: { type: 'number', minimum: 0 }, box: BOX_SCHEMA, conf: { type: 'number' } },
          },
        },
        span: { type: 'array', items: { type: 'number', minimum: 0 }, minItems: 2, maxItems: 2 },
        partial: { type: 'boolean' },
        lost_at: { type: ['number', 'null'] },
        reason: { type: ['string', 'null'] },
      },
    },
    still_seconds: { type: ['number', 'null'], minimum: 0 },
    still_box: { type: ['array', 'null'], items: { type: 'number', minimum: 0, maximum: 1000 }, minItems: 4, maxItems: 4 },
  },
};

export class StructuredEditError extends Error {
  status: 400 | 422;
  violations: ContractViolation[];
  notEditable: string[];

  constructor(status: 400 | 422, message: string, violations: ContractViolation[] = [], notEditable: string[] = []) {
    super(message);
    this.name = 'StructuredEditError';
    this.status = status;
    this.violations = violations;
    this.notEditable = notEditable;
  }
}

const compiledValidators = new Map<string, ValidateFunction>();

function editValidator(catalog?: ContractCatalog): ValidateFunction {
  const cat = catalog ?? getContractCatalog();
  const cacheKey = cat.contractVersion();
  let compiled = compiledValidators.get(cacheKey);
  if (compiled) return compiled;
  const contract = cat.schema() as {
    properties: {
      incident: { properties: Record<string, unknown> };
      entities: { items: { properties: Record<string, unknown> } };
      instruments: { items: { properties: Record<string, unknown> } };
      assets: { items: { properties: Record<string, unknown> } };
    };
  };
  const incidentProperties = contract.properties.incident.properties;
  const collectionSchema = (items: { properties: Record<string, unknown> }, fields: readonly string[]) => ({
    type: 'array',
    items: {
      type: 'object',
      additionalProperties: false,
      required: [...fields, 'source_id', 'image'],
      properties: {
        ...Object.fromEntries(fields.map((field) => [field, items.properties[field]])),
        source_id: SOURCE_ID_SCHEMA,
        image: IMAGE_SCHEMA,
        image_timestamp_seconds: IMAGE_TIMESTAMP_SCHEMA,
        image_box: IMAGE_BOX_SCHEMA,
        reviewer: REVIEWER_SCHEMA,
      },
    },
  });
  const editSchema = {
    type: 'object',
    additionalProperties: false,
    required: [...EDIT_COLLECTIONS],
    properties: {
      incident: {
        type: 'object',
        additionalProperties: false,
        required: [...EDITABLE_INCIDENT_FIELDS],
        properties: Object.fromEntries(EDITABLE_INCIDENT_FIELDS.map((field) => [field, incidentProperties[field]])),
      },
      entities: collectionSchema(contract.properties.entities.items, EDITABLE_ENTITY_FIELDS),
      instruments: collectionSchema(contract.properties.instruments.items, EDITABLE_INSTRUMENT_FIELDS),
      assets: collectionSchema(contract.properties.assets.items, EDITABLE_ASSET_FIELDS),
    },
  };
  compiled = new Ajv2020({ allErrors: true, strict: false }).compile(editSchema);
  compiledValidators.set(cacheKey, compiled);
  return compiled;
}

/** Keys the reviewer may not edit (Class B model output, Class C provenance, derived duration, confidence). */
export function notEditableKeys(edit: unknown): string[] {
  if (!edit || typeof edit !== 'object' || Array.isArray(edit)) return [];
  const blocked = Object.keys(edit).filter((key) => !(EDIT_COLLECTIONS as readonly string[]).includes(key));
  const incident = (edit as { incident?: unknown }).incident;
  if (incident && typeof incident === 'object' && !Array.isArray(incident)) {
    blocked.push(
      ...Object.keys(incident)
        .filter((key) => !(EDITABLE_INCIDENT_FIELDS as readonly string[]).includes(key))
        .map((key) => `incident.${key}`),
    );
  }
  return blocked;
}

/** The contract's span rule (schema description, not expressible in JSON Schema): first <= last, and the most-visible second lies within. */
function entitySpanViolations(edit: StructuredEdit): ContractViolation[] {
  return edit.entities.flatMap((entity, index): ContractViolation[] => {
    const { first_seen_seconds: first, last_seen_seconds: last, timestamp_seconds: seen } = entity;
    const bad =
      last < first
        ? 'last_seen_seconds is before first_seen_seconds'
        : seen < first || seen > last
          ? 'timestamp_seconds lies outside first_seen_seconds to last_seen_seconds'
          : null;
    return bad ? [{ code: 'SCHEMA_VIOLATION', path: `entities/${index}`, message: `entities[${index}]: ${bad}` }] : [];
  });
}

export function validateStructuredEdit(edit: unknown, catalog?: ContractCatalog): StructuredEdit {
  const blocked = notEditableKeys(edit);
  if (blocked.length) {
    throw new StructuredEditError(400, `Only structured fields can be edited; not editable: ${blocked.join(', ')}`, [], blocked);
  }
  const validate = editValidator(catalog);
  if (!validate(edit)) {
    const violations: ContractViolation[] = (validate.errors ?? []).map((error) => {
      const path = error.instancePath ? error.instancePath.slice(1) : '<root>';
      return { code: 'SCHEMA_VIOLATION', path, message: `${path}: ${error.message ?? error.keyword}` };
    });
    throw new StructuredEditError(
      422,
      `The edit does not satisfy the incident contract: ${violations.map((v) => v.message).join('; ')}`,
      violations,
    );
  }
  const typed = edit as StructuredEdit;
  // The contract's own cross-field rules; the timeline is model output and not part of an edit.
  const pseudoReport = { ...typed, incident: { ...typed.incident }, timeline: [], uncertainties: [] } as unknown as IncidentContractReport;
  const violations = [...crossFieldViolations(pseudoReport), ...entitySpanViolations(typed)];
  if (violations.length) {
    throw new StructuredEditError(
      422,
      `The edit does not satisfy the incident contract: ${violations.map((v) => v.message).join('; ')}`,
      violations,
    );
  }
  return typed;
}
