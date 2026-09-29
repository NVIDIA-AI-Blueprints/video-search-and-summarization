// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT
import { useMemo, useRef } from 'react';
import type { Analysis, BottleInfo } from './types';
import { bottleColor, clock } from './api';

export default function Chart({analysis, bottles, sceneId, currentTime, reference, onReference, seek, timeRange, referenceReadOnly=false}: {
  analysis: Analysis | null; bottles: BottleInfo[]; sceneId: string | null; currentTime: number; reference: number; onReference: (v: number) => void; seek: (v: number) => void; timeRange?: {start: number; end: number}; referenceReadOnly?: boolean;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const points = useMemo(() => sceneId ? analysis?.samples.filter(s => s.scene_id === sceneId && (!timeRange || (s.t >= timeRange.start && s.t <= timeRange.end))) || [] : [], [analysis, sceneId, timeRange?.start, timeRange?.end]);
  const start = points[0]?.t ?? 0, end = points.at(-1)?.t ?? 30;
  const width = 640, height = 224, left = 40, right = 18, top = 18, bottom = 30;
  const x = (t: number) => left + ((t - start) / Math.max(end - start, 1)) * (width - left - right);
  const y = (value: number) => top + (1 - value) * (height - top - bottom);
  const paths = bottles.map((bottle, index) => {
    let previous = false;
    const path = points.map(point => {
      const bottlePoint = point.bottles.find(b => b.id === bottle.id);
      if (!bottlePoint || bottlePoint.level === null) { previous = false; return ''; }
      const command = previous ? 'L' : 'M'; previous = true;
      return `${command}${x(point.t).toFixed(2)},${y(bottlePoint.level).toFixed(2)}`;
    }).join(' ');
    return {id: bottle.id, path, color: bottleColor(bottle.color, index)};
  });
  const pickTime = (clientX: number) => {
    const rect = svg.current?.getBoundingClientRect();
    if (!rect) return;
    const position = (clientX - rect.left) / rect.width * width;
    seek(Math.max(start, Math.min(end, start + (position - left) / (width - left - right) * (end - start))));
  };
  return <div className="chart-shell">
    {points.length ? <svg ref={svg} className="progress-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Visible liquid height over source video time. Click to seek." onClick={event => pickTime(event.clientX)}>
      <defs>{paths.map(p => <linearGradient key={p.id} id={`gradient-${p.id}`} x1="0" y1="0" x2="0" y2="1"><stop stopColor={p.color} stopOpacity=".17"/><stop offset="1" stopColor={p.color} stopOpacity="0"/></linearGradient>)}</defs>
      {[0, .25, .5, .75, 1].map(tick => <g key={tick}><line x1={left} x2={width-right} y1={y(tick)} y2={y(tick)} stroke="#2c342c" strokeDasharray={tick === 0 ? undefined : '3 6'}/><text x={left-10} y={y(tick)+4} textAnchor="end" className="chart-label">{Math.round(tick*100)}</text></g>)}
      {[0, .25, .5, .75, 1].map(tick => <text key={tick} x={x(start+(end-start)*tick)} y={height-7} textAnchor="middle" className="chart-label">{clock(start+(end-start)*tick)}</text>)}
      {paths.map(p => <path key={p.id} d={p.path} fill="none" stroke={p.color} strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round"/>)}
      <line x1={left} x2={width-right} y1={y(reference)} y2={y(reference)} stroke="#efcb7a" strokeDasharray="6 5" opacity=".8"/>
      <text x={width-right-4} y={Math.max(12, y(reference)-7)} textAnchor="end" fill="#efcb7a" fontSize="10">reference {referenceReadOnly?(reference*100).toFixed(2):Math.round(reference*100)}%</text>
      {currentTime >= start && currentTime <= end && <g><line x1={x(currentTime)} x2={x(currentTime)} y1={top} y2={height-bottom} stroke="#e9eee2" opacity=".5"/><circle cx={x(currentTime)} cy={height-bottom} r="3" fill="#e9eee2"/></g>}
    </svg> : <div className="chart-empty"><span>No measurements for this shot</span><p>Select a calibrated filling scene to compare visible liquid height.</p></div>}
    <div className="reference-control"><label htmlFor={referenceReadOnly?undefined:'reference-level'}>Reference line <strong>{referenceReadOnly?(reference*100).toFixed(2):Math.round(reference*100)}%</strong></label>{!referenceReadOnly&&<input id="reference-level" type="range" min="5" max="95" step="1" value={Math.round(reference*100)} onChange={event => onReference(Number(event.target.value)/100)}/>}<span>{referenceReadOnly?'Camera reference used for completed-cycle inspection':'Comparison marker · not a quality limit'}</span></div>
  </div>;
}
