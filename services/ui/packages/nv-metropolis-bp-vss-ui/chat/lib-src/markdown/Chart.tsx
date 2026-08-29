// SPDX-License-Identifier: MIT
/**
 * `<chart>` renderer for agent markdown.
 *
 * Payload shape is the toolkit's, unchanged, so a workflow that already emits
 * charts keeps working:
 *
 *   <chart>{"Label":"…","ChartType":"BarChart","Data":[…],"XAxisKey":"…"}</chart>
 *
 * The toolkit also had a `GraphPlot` type backed by react-force-graph-2d. That
 * dependency is not installed in this workspace and no VSS workflow emits the
 * type, so it renders as "unsupported" rather than pulling in a force-directed
 * graph engine for a case that never fires.
 */
import { IconDownload } from '@tabler/icons-react';
import React, { useCallback, useMemo } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

export interface ChartPayload {
  Label?: string;
  ChartType?: string;
  Data?: Record<string, unknown>[];
  XAxisKey?: string;
  YAxisKey?: string;
  ValueKey?: string;
  NameKey?: string;
  PolarAngleKey?: string;
  PolarValueKey?: string;
  BarKey?: string;
  LineKey?: string;
}

const BRAND = '#76b900';
const STROKE = '#1f2937';

/**
 * Fixed palette indexed by slice position.
 *
 * The toolkit generated a random colour per cell inside render, so every
 * re-render — one per streamed token — recoloured the pie. Deterministic
 * colours also mean a downloaded PNG matches what was on screen.
 */
const SLICE_COLORS = [
  '#76b900',
  '#0072ce',
  '#e57200',
  '#8a2be2',
  '#00a3a1',
  '#c2185b',
  '#f2c500',
  '#5c6bc0',
];

export const Chart: React.FC<{ payload: ChartPayload }> = ({ payload }) => {
  const {
    Label = '',
    ChartType = '',
    Data = [],
    XAxisKey = '',
    YAxisKey = '',
    ValueKey = '',
    NameKey = '',
    PolarAngleKey = '',
    PolarValueKey = '',
    BarKey = '',
    LineKey = '',
  } = payload ?? {};

  // Ids must be unique per chart on the page or the download grabs the wrong one.
  const domId = useMemo(
    () => `vss-chart-${(Label || 'untitled').replace(/\W+/g, '-')}-${ChartType || 'chart'}`,
    [Label, ChartType],
  );

  const handleDownload = useCallback(async () => {
    const element = document.getElementById(domId);
    if (!element) return;
    // html-to-image is only needed on an explicit click; keep it out of the
    // initial chunk.
    const htmlToImage = await import('html-to-image');
    const previousBackground = element.style.background;
    // Recharts draws on transparency; without this the PNG is unreadable in
    // any viewer that defaults to a dark backdrop.
    element.style.background = 'white';
    try {
      const dataUrl = await htmlToImage.toPng(element);
      const link = document.createElement('a');
      link.href = dataUrl;
      link.download = `${Label || 'chart'}-${ChartType || 'chart'}.png`;
      link.click();
    } catch (error) {
      console.warn('vss-chat: chart download failed', error);
    } finally {
      element.style.background = previousBackground;
    }
  }, [domId, Label, ChartType]);

  const chart = () => {
    switch (ChartType) {
      case 'BarChart':
        return (
          <BarChart data={Data}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey={XAxisKey} />
            <YAxis />
            <Tooltip />
            <Legend />
            <Bar dataKey={YAxisKey} fill={BRAND} />
          </BarChart>
        );
      case 'LineChart':
        return (
          <LineChart data={Data}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey={XAxisKey} />
            <YAxis />
            <Tooltip />
            <Legend />
            <Line type="monotone" dataKey={YAxisKey} stroke={BRAND} />
          </LineChart>
        );
      case 'PieChart':
        return (
          <PieChart>
            <Tooltip />
            <Legend />
            <Pie data={Data} dataKey={ValueKey} nameKey={NameKey} fill={BRAND} label>
              {Data.map((_entry, index) => (
                <Cell key={`cell-${index}`} fill={SLICE_COLORS[index % SLICE_COLORS.length]} />
              ))}
            </Pie>
          </PieChart>
        );
      case 'AreaChart':
        return (
          <AreaChart data={Data}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey={XAxisKey} />
            <YAxis />
            <Tooltip />
            <Legend />
            <Area type="monotone" dataKey={YAxisKey} stroke={STROKE} fill={BRAND} />
          </AreaChart>
        );
      case 'RadarChart':
        return (
          <RadarChart data={Data}>
            <PolarGrid />
            <PolarAngleAxis dataKey={PolarAngleKey} />
            <PolarRadiusAxis />
            <Radar
              name="Metrics"
              dataKey={PolarValueKey}
              stroke={STROKE}
              fill={BRAND}
              fillOpacity={0.6}
            />
            <Legend />
          </RadarChart>
        );
      case 'ScatterChart':
        return (
          <ScatterChart>
            <CartesianGrid />
            <XAxis type="number" dataKey={XAxisKey} name={XAxisKey} />
            <YAxis type="number" dataKey={YAxisKey} name={YAxisKey} />
            <Tooltip cursor={{ strokeDasharray: '3 3' }} />
            <Legend />
            <Scatter name={Label || 'Series'} data={Data} fill={BRAND} />
          </ScatterChart>
        );
      case 'ComposedChart':
        return (
          <ComposedChart data={Data}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey={XAxisKey} />
            <YAxis />
            <Tooltip />
            <Legend />
            <Bar dataKey={BarKey} fill={BRAND} />
            <Line type="monotone" dataKey={LineKey} stroke={STROKE} />
          </ComposedChart>
        );
      default:
        return null;
    }
  };

  const body = chart();
  if (!body) {
    return (
      <div className="my-2 rounded-md border border-dashed border-gray-300 p-3 text-sm text-gray-500 dark:border-gray-600 dark:text-gray-400">
        Unsupported chart type: {ChartType || '(none)'}
      </div>
    );
  }

  return (
    <div className="relative my-2 pb-2">
      <button
        type="button"
        onClick={handleDownload}
        title="Download chart"
        aria-label="Download chart"
        className="absolute right-2 top-2 z-10 rounded p-1 text-gray-500 hover:text-[#76b900] dark:text-gray-400"
      >
        <IconDownload className="h-4 w-4" />
      </button>
      <div id={domId} className="pt-4">
        {Label ? <div className="pl-4 font-medium">{Label}</div> : null}
        <ResponsiveContainer width="100%" height={300} className="p-2">
          {body}
        </ResponsiveContainer>
      </div>
    </div>
  );
};

export default Chart;
