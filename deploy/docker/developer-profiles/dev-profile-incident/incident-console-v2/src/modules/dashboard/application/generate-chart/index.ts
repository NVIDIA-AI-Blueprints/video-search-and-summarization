// SPDX-License-Identifier: Apache-2.0

// Generate chart: one request in, one chart out. The model only picks a spec from
// the allowlist; the figures come from getDashboard, so a generated chart counts
// exactly what the dashboard counts; and VSS chart_generator draws those figures.

import type { ChartInterpreter } from '@/modules/dashboard/domain/ChartInterpreter';
import type { ChartRenderer } from '@/modules/dashboard/domain/ChartRenderer';
import {
  CHART_DIMENSIONS,
  CHART_PERIODS,
  type ChartDimensionName,
  type ChartMetric,
  type ChartPeriod,
  type ChartSpec,
  parseChartSpec,
} from '@/modules/dashboard/domain/chart-spec';
import type { DashboardQuery, DashboardRepository } from '@/modules/dashboard/domain/DashboardRepository';
import type {
  ChartAnswer,
  ChartRow,
  ChartSeries,
  ChartType,
  GeneratedChart,
  ReportTotals,
} from '@/modules/dashboard/domain/generated-chart';
import { normalizeSeverityWords } from '@/modules/dashboard/domain/severity-words';
import type { DashboardAnalytics, RankedValue } from '@/modules/dashboard/domain/types';

const COUNT = 'count';
const STATUS_SERIES: ChartSeries[] = [
  { key: 'official', label: 'Official' },
  { key: 'not_official', label: 'Not official' },
];
const VALUE_LABELS: Record<ChartMetric, string> = { incidents: 'Reports', instruments: 'Instruments', analyses: 'Analyses' };
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const PERIOD_LABELS: Record<ChartPeriod, string> = {
  last_7_days: 'Last 7 days',
  last_30_days: 'Last 30 days',
  last_90_days: 'Last 90 days',
  last_365_days: 'Last 365 days',
  all_time: 'All time',
};
const DAY_MS = 86_400_000;
/** chart_generator labels every bar, so past this many a trend moves to the next coarser bucket. */
const MAX_BUCKETS = 31;

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);
const countRow = (category: string, count: number): ChartRow => ({ category, values: { [COUNT]: count } });
const rankedRows = (items: RankedValue[]) => items.map((item) => countRow(capitalize(item.label), item.count));

function dashboardQuery(spec: ChartSpec, timezone: string, now: Date): DashboardQuery {
  const days = CHART_PERIODS[spec.period];
  return {
    periodStart: days === null ? null : new Date(now.getTime() - days * DAY_MS),
    periodEnd: days === null ? null : now,
    type: spec.incidentType,
    severity: spec.severity,
    dayOfWeek: null,
    hour: null,
    timezone,
    scope: 'all',
  };
}

function bucketOf(date: string, dimension: ChartDimensionName): string {
  if (dimension === 'report_month') return date.slice(0, 7);
  if (dimension !== 'report_week') return date;
  const day = new Date(`${date}T00:00:00Z`);
  const daysSinceMonday = (day.getUTCDay() + 6) % 7;
  return new Date(day.getTime() - daysSinceMonday * DAY_MS).toISOString().slice(0, 10);
}

/** The RPC's daily rows summed into the bucket; each day in it is zero-filled, so a bucket with no reports stays visible. */
function trendRows(dimension: ChartDimensionName, trend: DashboardAnalytics['trend']): ChartRow[] {
  const buckets = new Map<string, number>();
  for (const { date, count } of trend) {
    const bucket = bucketOf(date, dimension);
    buckets.set(bucket, (buckets.get(bucket) ?? 0) + count);
  }
  return Array.from(buckets, ([category, count]) => countRow(category, count));
}

/** The dimension a trend is drawn at: the one asked for, or a coarser one when that would be too many bars. */
function trendDimension(dimension: ChartDimensionName, trend: DashboardAnalytics['trend']): ChartDimensionName {
  if (dimension === 'report_day' && trendRows(dimension, trend).length > MAX_BUCKETS) return trendDimension('report_week', trend);
  if (dimension === 'report_week' && trendRows(dimension, trend).length > MAX_BUCKETS) return 'report_month';
  return dimension;
}

function heatmapRows(analytics: DashboardAnalytics, field: 'day' | 'hour', labels: string[]): ChartRow[] {
  const counts = labels.map(() => 0);
  for (const cell of analytics.heatmap) {
    const index = cell[field];
    if (index !== null && index in counts) counts[index] = (counts[index] ?? 0) + cell.count;
  }
  return labels.map((label, index) => countRow(label, counts[index] ?? 0));
}

function rowsFor(dimension: ChartDimensionName, analytics: DashboardAnalytics, reports: ReportTotals | null): ChartRow[] {
  const coverage = analytics.modelCoverage;
  const sum = (pick: (row: DashboardAnalytics['modelCoverage'][number]) => number) => coverage.reduce((total, row) => total + pick(row), 0);
  switch (dimension) {
    case 'incident_type':
      return analytics.types.map((item) => countRow(capitalize(item.type), item.count));
    case 'severity':
      return [
        countRow('Low', analytics.severity.low),
        countRow('Medium', analytics.severity.medium),
        countRow('High', analytics.severity.high),
      ];
    case 'report_status':
      return [countRow('Official', reports?.official ?? 0), countRow('Not official', reports?.notOfficial ?? 0)];
    case 'report_kind':
      return [countRow('AI as generated', analytics.revisions.ai), countRow('Human-edited', analytics.revisions.human)];
    case 'report_day':
    case 'report_week':
    case 'report_month':
      return trendRows(dimension, analytics.trend);
    case 'report_weekday':
      return heatmapRows(analytics, 'day', WEEKDAYS);
    case 'report_hour':
      return heatmapRows(
        analytics,
        'hour',
        Array.from({ length: 24 }, (_, hour) => `${String(hour).padStart(2, '0')}:00`),
      );
    case 'entity_type':
      return rankedRows(analytics.entityTypes);
    case 'instrument':
      return rankedRows(analytics.topInstruments);
    case 'asset':
      return rankedRows(analytics.topAssets);
    case 'confidence': {
      const buckets = new Map(analytics.confidence.map((item) => [item.bucket, item.count]));
      return Array.from({ length: 10 }, (_, bucket) => countRow(`${bucket * 10}-${(bucket + 1) * 10}%`, buckets.get(bucket) ?? 0));
    }
    case 'threat_level': {
      const levels = new Map(analytics.threatLevels.map((item) => [item.level, item.count]));
      return [1, 2, 3, 4, 5].map((level) => countRow(`Threat ${level}`, levels.get(level) ?? 0));
    }
    case 'model':
      return coverage.map((row) => countRow(row.model, row.withReport + row.contractFailed + row.requestFailed));
    case 'analysis_outcome':
      return [
        countRow(
          'Valid',
          sum((row) => row.validFirstPass),
        ),
        countRow(
          'IDs repaired',
          sum((row) => row.repaired),
        ),
        countRow(
          'Earlier',
          sum((row) => row.legacy),
        ),
        countRow(
          'Contract failed',
          sum((row) => row.contractFailed),
        ),
        countRow(
          'Request failed',
          sum((row) => row.requestFailed),
        ),
      ];
  }
}

/** The chart's rows with one count each for official and not official, on the axis of the combined query. */
async function splitRows(
  dimension: ChartDimensionName,
  rows: ChartRow[],
  analytics: DashboardAnalytics,
  query: DashboardQuery,
  dashboard: DashboardRepository,
): Promise<ChartRow[]> {
  if (dimension === 'asset') {
    return analytics.topAssets.map((item) => ({
      category: capitalize(item.label),
      values: { official: item.official, not_official: item.notOfficial },
    }));
  }
  const [official, notOfficial] = await Promise.all(
    (['official', 'not_official'] as const).map(async (scope) => {
      const scoped = await dashboard.getDashboard({ ...query, scope });
      return new Map(rowsFor(dimension, scoped, null).map((row) => [row.category, row.values[COUNT] ?? 0]));
    }),
  );
  return rows.map(({ category }) => ({
    category,
    values: { official: official?.get(category) ?? 0, not_official: notOfficial?.get(category) ?? 0 },
  }));
}

export interface GenerateChartPorts {
  interpreter: ChartInterpreter;
  dashboard: DashboardRepository;
  renderer: ChartRenderer;
}

export interface GenerateChartInput {
  request: string;
  chartType: ChartType;
  timezone: string;
  now: Date;
}

export async function generateChart(
  { interpreter, dashboard, renderer }: GenerateChartPorts,
  { request, chartType, timezone, now }: GenerateChartInput,
): Promise<ChartAnswer> {
  // Canonical severity words become their band in code (domain/severity-words.ts); the model reads the result.
  const spec = parseChartSpec(await interpreter.interpret(normalizeSeverityWords(request)));
  if (!spec) return { kind: 'unsupported' };
  if (chartType === 'pie' && !CHART_DIMENSIONS[spec.dimension].pie) return { kind: 'needs_bar' };

  const query = dashboardQuery(spec, timezone, now);
  const analytics = await dashboard.getDashboard(query);
  const reports =
    spec.metric === 'analyses' ? null : { total: analytics.total, official: analytics.official, notOfficial: analytics.notOfficial };
  const dimensionName = trendDimension(spec.dimension, analytics.trend);
  const dimension = CHART_DIMENSIONS[dimensionName];
  const rows = rowsFor(dimensionName, analytics, reports);
  const split = chartType === 'bar' && dimension.splits;
  const valueLabel = VALUE_LABELS[spec.metric];
  const chart: GeneratedChart = {
    chartType,
    title: dimension.title,
    description: dimension.description,
    categoryLabel: dimension.categoryLabel,
    valueLabel,
    scope: [
      PERIOD_LABELS[spec.period],
      ...(spec.incidentType ? [capitalize(spec.incidentType)] : []),
      ...(spec.severity ? [`${capitalize(spec.severity)} severity`] : []),
    ],
    series: split ? STATUS_SERIES : [{ key: COUNT, label: valueLabel }],
    rows: split ? await splitRows(dimensionName, rows, analytics, query, dashboard) : rows,
    reports,
    image: null,
  };
  const hasData = chart.rows.some((row) => Object.values(row.values).some(Boolean));
  return { kind: 'chart', chart: hasData ? { ...chart, image: await renderer.draw(chart) } : chart };
}
