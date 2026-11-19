// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
export const triggerFields = (type?: string) => type === 'noul' ? ['noul'] : type === 'score' ? ['score', 'probability'] : type === 'choice' ? ['choice', 'probability'] : [];
export const comparisons = (field: string) => field === 'choice' ? ['eq'] : field === 'noul' ? ['gte', 'lte'] : ['gte', 'lte', 'eq'];
export const comparisonLabel = (operator: string) => operator === 'gte' ? '≥ threshold' : operator === 'lte' ? '≤ threshold' : '= value';
export function triggerError(question: Record<string, any> | undefined, rule: Record<string, any>) {
  if (!triggerFields(question?.type).includes(rule.field)) return 'Choose a trigger field that matches the D1 answer type.';
  if (!comparisons(rule.field).includes(rule.operator)) return 'Choose a supported comparison for this trigger.';
  const value = Number(rule.value);
  if (rule.field !== 'choice' && !Number.isFinite(value)) return 'Enter a numeric trigger threshold.';
  if (rule.field === 'noul' && !(rule.operator === 'gte' && value >= .5 && value <= 1 || rule.operator === 'lte' && value >= 0 && value <= .5)) return 'Yes requires a threshold from 0.5 to 1; No requires a threshold from 0 to 0.5.';
  if (rule.field === 'probability' && (value < 0 || value > 1)) return 'Probability thresholds must be from 0 to 1.';
  if (rule.field === 'score' && rule.operator === 'eq' && !Number.isInteger(value)) return 'An exact score must be an integer label index.';
  const criteria = question?.criteria;
  if (rule.field === 'choice' || rule.field === 'probability') {
    const key = rule.field === 'choice' ? rule.value : rule.probability_key;
    if (key == null || !criteria || !Object.prototype.hasOwnProperty.call(criteria, String(key))) return 'Choose a key from the configured answer labels.';
  }
  if (rule.field === 'score' && (!Array.isArray(criteria) || !criteria.some((_label, index) => rule.operator === 'eq' ? index === value : rule.operator === 'gte' ? index >= value : index <= value))) return 'This threshold does not match any configured answer label.';
  return '';
}
