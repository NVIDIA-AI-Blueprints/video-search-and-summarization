// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT
export type Job = { status: 'idle' | 'running' | 'complete' | 'error'; progress: number; error?: string };
export type Health = {status: string; vss: { configured: boolean; available: boolean; base_url: string | null }; analysis: Job };
export type Chapter = {id: string; label: string; start: number; end: number; kind: 'reference'; description: string};
export type Source = {id: string; name: string; duration: number; width: number; height: number; fps: number; sha256: string; media_url: string; mode: 'recorded'; analysis_kind?: 'bottle-cycles'; origin?: string; sensor_id?: string; stream_id?: string; recording_start?: string; recording_end?: string; actual_start_time?: string; identity_proof?: unknown; chapters: Chapter[]};
export type Bottle = {id: string; label: string; box: [number, number, number, number]; measurement_box?: [number, number, number, number]; measurement_state?: string; level_kind?: string; reason?: string; level: number | null; confidence: number; surface: [number, number][]; mask: [number, number][]};
export type Sample = {t: number; scene_id: string | null; phase: string; bottles: Bottle[]};
export type BottleInfo = {id: string; label: string; color: string; scene_id: string};
export type Event = {id: string; t: number; type: string; label: string; detail: string; bottle_id?: string};
export type CycleStatus = 'normal' | 'underfill' | 'overflow' | 'uncertain';
export type BottleCycle = {id: string; label: string; start_time: number; end_time: number; fill_start_time: number | null; fill_end_time: number | null; measurement_time: number | null; final_level: number | null; status: CycleStatus; confidence: number; reason: string; evidence_start: number; evidence_end: number; reference_level: number; tolerance: number; overflow_time: number | null};
export type CycleSummary = {total: number; normal: number; underfill: number; overflow: number; uncertain: number};
export type Analysis = {analysis_kind?: 'bottle-cycles'; cycles?: BottleCycle[]; summary?: CycleSummary; version: string; source_sha256: string; algorithm: string; sample_fps: number; runtime_seconds: number; samples: Sample[]; events: Event[]; bottles: BottleInfo[]; quality: Record<string, unknown>; provenance?: {models?: Record<string, unknown>; segmentation_cache_key?: string; [key: string]: unknown}};
export type QueryResult = {answer: string; evidence: {t: number; label: string}[]; method: string; supported: boolean};
