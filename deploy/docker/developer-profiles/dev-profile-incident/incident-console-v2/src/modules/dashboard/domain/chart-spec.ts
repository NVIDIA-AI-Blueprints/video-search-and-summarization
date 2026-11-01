// SPDX-License-Identifier: Apache-2.0

// The allowlist for generated charts. A model reply is untrusted input: it counts
// only if it parses as a ChartSpec, which holds nothing but values from the lists
// below, so no model text reaches the database or the screen. Anything else,
// including extra keys such as data or a title, is refused as a whole.

import { z } from 'zod';

import { INCIDENT_TYPES } from '@/modules/contract/domain/types';
import type { DashboardSeverity } from '@/modules/dashboard/domain/DashboardRepository';

export const CHART_METRICS = ['incidents', 'instruments', 'analyses'] as const;
export type ChartMetric = (typeof CHART_METRICS)[number];

export const CHART_SEVERITIES = ['low', 'medium', 'high'] as const satisfies readonly DashboardSeverity[];

/** Days back from now; null is all time. */
export const CHART_PERIODS = {
  last_7_days: 7,
  last_30_days: 30,
  last_90_days: 90,
  last_365_days: 365,
  all_time: null,
} as const;
export type ChartPeriod = keyof typeof CHART_PERIODS;
const PERIOD_NAMES = Object.keys(CHART_PERIODS) as [ChartPeriod, ...ChartPeriod[]];

interface ChartDimension {
  metric: ChartMetric;
  /** The parts make one whole, so a pie is honest. A trend, or a list where one incident counts under several names, is not. */
  pie: boolean;
  /**
   * A bar chart can show official and not official beside each other. False for top
   * instruments, which the query cuts to eight per scope, so a name could drop out of
   * one scope and read as 0, and for analysis attempts, which are not reports.
   */
  splits: boolean;
  /** Period only for false: get_incident_dashboard applies no other filter to analysis attempts. */
  filterable: boolean;
  title: string;
  description: string;
  categoryLabel: string;
  /** How the interpreter is told to recognise this dimension. */
  meaning: string;
}

const REPORTS = 'Each video counts once: its official report, else its newest.';
const ATTEMPTS = 'Every analysis attempt in the period, all runs and not only reports.';
const NOT_INCIDENT_TIME = 'This is not when an incident happened.';

export const CHART_DIMENSIONS = {
  incident_type: {
    metric: 'incidents',
    pie: true,
    splits: true,
    filterable: true,
    title: 'Reports by incident type',
    description: REPORTS,
    categoryLabel: 'Incident type',
    meaning: 'count per incident type: "incident distribution", "what types of incident"',
  },
  severity: {
    metric: 'incidents',
    pie: true,
    splits: true,
    filterable: true,
    title: 'Reports by severity',
    description: `${REPORTS} Low is level 1-2, medium is 3, high is 4-5.`,
    categoryLabel: 'Severity',
    meaning: 'count per severity band: "severity breakdown"',
  },
  report_status: {
    metric: 'incidents',
    pie: true,
    splits: false,
    filterable: true,
    title: 'Official and not official reports',
    description: `${REPORTS} Not official means no report has been marked official yet.`,
    categoryLabel: 'Status',
    meaning: 'how many reports are official and how many are not',
  },
  report_kind: {
    metric: 'incidents',
    pie: true,
    splits: true,
    filterable: true,
    title: 'Reports by version',
    description: `${REPORTS} As the model generated them, or saved by a reviewer.`,
    categoryLabel: 'Report version',
    meaning: 'AI-generated versus human-edited reports',
  },
  report_day: {
    metric: 'incidents',
    pie: false,
    splits: true,
    filterable: true,
    title: 'Reports per day',
    description: `${REPORTS} By report date.`,
    categoryLabel: 'Day',
    meaning: 'count per day of the report date: "trends over time", "daily incidents"',
  },
  report_week: {
    metric: 'incidents',
    pie: false,
    splits: true,
    filterable: true,
    title: 'Reports per week',
    description: `${REPORTS} By report date, in weeks from Monday.`,
    categoryLabel: 'Week starting',
    meaning: 'count per week of the report date',
  },
  report_month: {
    metric: 'incidents',
    pie: false,
    splits: true,
    filterable: true,
    title: 'Reports per month',
    description: `${REPORTS} By report date.`,
    categoryLabel: 'Month',
    meaning: 'count per month of the report date',
  },
  report_weekday: {
    metric: 'incidents',
    pie: false,
    splits: true,
    filterable: true,
    title: 'Report activity by weekday',
    description: `Reports by the weekday they were generated, in your time zone. ${NOT_INCIDENT_TIME}`,
    categoryLabel: 'Weekday',
    meaning: 'reports per weekday they were generated, never the weekday an incident happened',
  },
  report_hour: {
    metric: 'incidents',
    pie: false,
    splits: true,
    filterable: true,
    title: 'Report activity by hour',
    description: `Reports by the hour they were generated, in your time zone. ${NOT_INCIDENT_TIME}`,
    categoryLabel: 'Hour',
    meaning: 'reports per hour they were generated, never the hour an incident happened',
  },
  entity_type: {
    metric: 'incidents',
    pie: false,
    splits: true,
    filterable: true,
    title: 'Reports by entity type',
    description: 'Reports involving each entity type. A report can count under more than one.',
    categoryLabel: 'Entity type',
    meaning: 'incidents involving humans, animals or unknown entities',
  },
  instrument: {
    metric: 'incidents',
    pie: false,
    splits: false,
    filterable: true,
    title: 'Top instruments',
    description: 'The eight most common instruments, by reports naming each. A report can count under more than one.',
    categoryLabel: 'Instrument',
    meaning: 'most common instruments or weapons',
  },
  asset: {
    metric: 'incidents',
    pie: false,
    splits: true,
    filterable: true,
    title: 'Top assets',
    description: 'The eight most common assets, by reports naming each. A report can count under more than one.',
    categoryLabel: 'Asset',
    meaning: 'most commonly affected assets',
  },
  confidence: {
    metric: 'incidents',
    pie: false,
    splits: true,
    filterable: true,
    title: 'Model confidence distribution',
    description: 'Reports by the confidence the model gave. Reports with no confidence value are left out.',
    categoryLabel: 'Confidence',
    meaning: 'histogram of model confidence scores',
  },
  threat_level: {
    metric: 'instruments',
    pie: false,
    splits: true,
    filterable: true,
    title: 'Instrument threat levels',
    description: 'Instruments in the counted reports, by rated threat.',
    categoryLabel: 'Threat level',
    meaning: 'count of instruments per threat level',
  },
  model: {
    metric: 'analyses',
    pie: false,
    splits: false,
    filterable: false,
    title: 'Analyses by model',
    description: ATTEMPTS,
    categoryLabel: 'Model',
    meaning: 'analysis attempts per model',
  },
  analysis_outcome: {
    metric: 'analyses',
    pie: true,
    splits: false,
    filterable: false,
    title: 'Analyses by outcome',
    description: ATTEMPTS,
    categoryLabel: 'Outcome',
    meaning: 'analysis attempts per outcome: valid, repaired, failed',
  },
} as const satisfies Record<string, ChartDimension>;

export type ChartDimensionName = keyof typeof CHART_DIMENSIONS;
const DIMENSION_NAMES = Object.keys(CHART_DIMENSIONS) as [ChartDimensionName, ...ChartDimensionName[]];

const chartSpecSchema = z
  .object({
    metric: z.enum(CHART_METRICS),
    dimension: z.enum(DIMENSION_NAMES),
    period: z.enum(PERIOD_NAMES),
    incidentType: z.enum(INCIDENT_TYPES).nullable(),
    severity: z.enum(CHART_SEVERITIES).nullable(),
  })
  .strict()
  .refine((spec) => {
    const dimension: ChartDimension = CHART_DIMENSIONS[spec.dimension];
    return dimension.metric === spec.metric && (dimension.filterable || (spec.incidentType === null && spec.severity === null));
  });

export type ChartSpec = z.infer<typeof chartSpecSchema>;

/** The spec a model reply describes, or null for anything else: a refusal, a non-allowlisted value or an unknown key. */
export function parseChartSpec(reply: unknown): ChartSpec | null {
  const parsed = chartSpecSchema.safeParse(reply);
  return parsed.success ? parsed.data : null;
}

/** Every allowlisted value, as exported to contracts/prompts/chart/allowlist.json for evaluation (drift-tested). */
export function chartAllowlist(): Record<string, unknown> {
  return {
    metrics: [...CHART_METRICS],
    severities: [...CHART_SEVERITIES],
    periods: { ...CHART_PERIODS },
    incidentTypes: [...INCIDENT_TYPES],
    dimensions: CHART_DIMENSIONS,
  };
}
