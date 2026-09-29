// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT
import type { BottleCycle, CycleStatus } from './types';
import { clock } from './api';
import { ArrowUpRight, Crosshair, Film, MessageSquare, ShieldCheck } from './icons';

export const cycleStatusLabel: Record<CycleStatus, string> = {
  normal: 'Normal', underfill: 'Underfill', overflow: 'Overflow', uncertain: 'Uncertain',
};
export const cycleLevel = (level: number | null) => level === null ? 'Unreadable' : `${(level * 100).toFixed(1)}%`;
export const cycleQuestion = 'Which bottles finished below the reference level? Show their evidence.';

export function CycleSummary({cycles, neuralMeasurements = false}: {cycles: BottleCycle[]; neuralMeasurements?: boolean}) {
  const counts = {normal: 0, underfill: 0, overflow: 0, uncertain: 0};
  cycles.forEach(cycle => { counts[cycle.status] += 1; });
  return <section className="cycle-summary" aria-label="Recorded bottle inspection totals">
    <div className="cycle-total"><span>Analyzed bottles</span><strong>{cycles.length}</strong><small>{neuralMeasurements?'RF-DETR height analysis':'Recorded inspection'}</small></div>
    {(Object.keys(counts) as CycleStatus[]).map(status => <div key={status} className={`cycle-count cycle-${status}`}><span>{cycleStatusLabel[status]}</span><strong>{counts[status]}</strong><small>{status === 'uncertain' ? 'Needs review' : status === 'overflow' ? 'Exterior-liquid check' : status === 'underfill' ? 'Below final-height reference' : 'Within final-height reference'}</small></div>)}
  </section>;
}

export function CycleDetail({cycle, currentLevel, evidence, askAgent, neuralMeasurements = false}: {
  neuralMeasurements?: boolean; cycle?: BottleCycle; currentLevel: number | null; evidence: (cycle: BottleCycle) => void; askAgent?: () => void;
}) {
  return <section className="panel cycle-detail">
    <div className="panel-topline"><div className="panel-title"><Crosshair size={16}/><h2>Selected bottle</h2></div><span className="pill subtle">RECORDED INSPECTION</span></div>
    {cycle ? <div className="cycle-detail-body">
      <div className="cycle-detail-heading"><h3>{cycle.label}</h3><span className={`cycle-status cycle-${cycle.status}`}>{cycleStatusLabel[cycle.status]}</span></div>
      <span className="eyebrow">FINAL VISIBLE LIQUID HEIGHT</span><div className="cycle-final-level">{cycleLevel(cycle.final_level)}</div>
      <p className="cycle-result-time">{cycle.measurement_time===null?'Final height not established':`Final assessment at ${clock(cycle.measurement_time, true)} · source time`}</p>
      <dl className="cycle-facts"><div><dt>Reference height</dt><dd>{cycleLevel(cycle.reference_level)}</dd></div><div><dt>Reference tolerance</dt><dd>±{(cycle.tolerance * 100).toFixed(1)} percentage points</dd></div><div><dt>Cycle interval</dt><dd>{clock(cycle.start_time, true)}–{clock(cycle.end_time, true)}</dd></div><div><dt>Height at playhead</dt><dd>{cycleLevel(currentLevel)}</dd></div>{cycle.overflow_time !== null && <div><dt>Exterior liquid observed</dt><dd>{clock(cycle.overflow_time, true)}</dd></div>}</dl>
      <p className="cycle-reason">{cycle.reason}</p>
      <div className="button-row"><button className="button primary" onClick={() => evidence(cycle)}><Film size={14}/>View bottle evidence</button>{askAgent && <button className="button secondary" onClick={askAgent}><MessageSquare size={14}/>Ask VSS Agent</button>}</div>
      <div className="measurement-note"><ShieldCheck size={15}/><p>{neuralMeasurements?'Final height and underfill are derived from RF-DETR bottle and liquid masks. Overflow uses a separate exterior-color check.':'Legacy calibrated final-height assessment for this camera; RF-DETR masks are separate.'} Visible height is not liquid volume or a factory quality specification.</p></div>
    </div> : <div className="tracking-empty"><Crosshair size={28}/><h3>No completed bottle result</h3><p>Select an analyzed bottle to review its final height and evidence.</p></div>}
  </section>;
}

export function CycleResults({cycles, selectedId, time, duration, select, evidence}: {
  cycles: BottleCycle[]; selectedId: string | null; time: number; duration: number; select: (cycle: BottleCycle) => void; evidence: (cycle: BottleCycle) => void;
}) {
  return <section className="panel cycle-results" id="timeline">
    <div className="panel-topline"><div className="panel-title"><Film size={16}/><h2>Bottle inspection results</h2></div><span className="small-note">Select a bottle to inspect its source footage</span></div>
    <div className="cycle-timeline" aria-label="Bottle cycles across the recording">
      {cycles.map(cycle => <button key={cycle.id} className={`cycle-segment cycle-${cycle.status} ${selectedId === cycle.id ? 'active' : ''}`} title={`${cycle.label}: ${cycleStatusLabel[cycle.status]}, ${clock(cycle.start_time)}–${clock(cycle.end_time)}`} aria-label={`Inspect ${cycle.label}, ${cycleStatusLabel[cycle.status]}`} aria-pressed={selectedId === cycle.id} style={{left: `${cycle.start_time / Math.max(duration, 1) * 100}%`, width: `${Math.max(.35, (cycle.end_time - cycle.start_time) / Math.max(duration, 1) * 100)}%`}} onClick={() => select(cycle)}><span>{cycle.label.replace(/^Bottle\s*/i, '')}</span></button>)}
      <i className="cycle-playhead" style={{left: `${Math.min(100, Math.max(0, time / Math.max(duration, 1) * 100))}%`}}/>
    </div>
    <div className="cycle-timeline-labels"><span>00:00</span><span>{clock(duration)}</span></div>
    <div className="cycle-table-scroll"><table className="cycle-table"><thead><tr><th scope="col">Bottle</th><th scope="col">Cycle</th><th scope="col">Final height</th><th scope="col">Assessment</th><th scope="col">Evidence</th></tr></thead><tbody>
      {cycles.map(cycle => <tr key={cycle.id} className={selectedId === cycle.id ? 'selected' : ''}><th scope="row"><button className="text-button" onClick={() => select(cycle)} aria-label={`Seek to ${cycle.label} final assessment`}>{cycle.label}<ArrowUpRight size={12}/></button></th><td>{clock(cycle.start_time)}–{clock(cycle.end_time)}</td><td>{cycleLevel(cycle.final_level)}</td><td><span className={`cycle-status cycle-${cycle.status}`}>{cycleStatusLabel[cycle.status]}</span></td><td><button className="text-button" onClick={() => evidence(cycle)} aria-label={`Open ${cycle.label} evidence`}><Film size={13}/> {clock(cycle.evidence_start)}–{clock(cycle.evidence_end)}</button></td></tr>)}
    </tbody></table>{!cycles.length && <p className="cycle-table-empty">No bottle cycles were established in this analysis. No inspection verdicts are available.</p>}</div>
    <p className="cycle-table-note">Computed from the selected recording. Underfill is assessed at the end of filling; a rising liquid level during filling is not an underfill.</p>
  </section>;
}
