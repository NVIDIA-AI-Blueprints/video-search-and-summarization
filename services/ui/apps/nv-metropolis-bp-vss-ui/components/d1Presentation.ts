// SPDX-FileCopyrightText: Copyright (c) 2026 D1 project contributors
// SPDX-License-Identifier: Apache-2.0

/** Match persisted event provenance, never an unrelated source's event. */
export function matchIncident(job: any, incidents: any[]): any | null {
  if (typeof job?.id !== 'string' || !job.id || typeof job?.stream_id !== 'string' || !job.stream_id || !Array.isArray(incidents)) return null;
  return incidents.find(incident => incident?.id === job.id && incident?.stream_id === job.stream_id) ?? null;
}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
// Remove floating-point display noise while retaining meaningful fractional values.
const numberText = (value: number) => Number(value.toPrecision(12)).toString();
const percentage = (value: number) => `${numberText(value * 100)}%`;

/** Explain only the rule saved with this incident, not the current configuration. */
export function describeTrigger(incident: any): string | null {
  const rule = incident?.rule;
  if (!rule || typeof rule !== 'object' || Array.isArray(rule)) return null;
  const count = rule.consecutive_samples;
  if (!Number.isInteger(count) || count < 1 || count > 100) return null;
  if (rule.stream_id && incident.stream_id && rule.stream_id !== incident.stream_id) return null;
  if (rule.config_revision !== undefined && incident.trigger?.config_revision !== undefined && rule.config_revision !== incident.trigger.config_revision) return null;
  const checks = count === 1 ? 'for 1 check' : `for ${count} consecutive checks`;
  const comparison = rule.operator === 'gte' ? 'at least' : rule.operator === 'lte' ? 'at most' : rule.operator === 'eq' ? 'exactly' : null;
  if (!comparison) return null;

  if (rule.field === 'choice') {
    if (rule.operator !== 'eq' || typeof rule.value !== 'string' || !rule.value.trim()) return null;
    return `D1 chose “${rule.value}” ${checks}.`;
  }
  if (!finite(rule.value)) return null;
  if (rule.field === 'score') return `D1’s score was ${comparison} ${numberText(rule.value)} ${checks}.`;
  if (!['noul', 'confidence', 'probability'].includes(rule.field) || rule.value < 0 || rule.value > 1) return null;
  if (rule.field === 'probability' && (typeof rule.probability_key !== 'string' || !rule.probability_key.trim())) return null;
  const metric = rule.field === 'noul' ? 'yes score' : rule.field === 'confidence' ? 'confidence' : `probability for “${rule.probability_key}”`;
  return `D1’s ${metric} was ${comparison} ${percentage(rule.value)} ${checks}.`;
}

/** The question attached to the triggering decision, including original wording. */
export function eventTriggerQuestion(incident: any): string | null {
  const instructions = incident?.trigger?.question?.instructions;
  return typeof instructions === 'string' && instructions.trim() ? instructions : null;
}

/** Requested pre/post window; the actual exported media duration can differ. */
export function describeEventWindow(incident: any): string | null {
  const before = incident?.rule?.pre_seconds;
  const after = incident?.rule?.post_seconds;
  if (!finite(before) || !finite(after) || before < 0 || after < 0 || before > 120 || after > 120 || before + after <= 0) return null;
  const duration = (seconds: number) => `${numberText(seconds)} ${seconds === 1 ? 'second' : 'seconds'}`;
  return `${duration(before)} before + ${duration(after)} after the event`;
}
