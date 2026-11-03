// SPDX-License-Identifier: Apache-2.0

export type StageKey = 'idle' | 'uploading' | 'converting' | 'analyzing' | 'saving' | 'complete' | 'error';

export const stageLabels: Array<{ key: StageKey; label: string }> = [
  { key: 'uploading', label: 'Uploading videos' },
  { key: 'analyzing', label: 'Understanding the incident' },
  { key: 'saving', label: 'Structuring and saving report' },
  { key: 'complete', label: 'Report ready' },
];

/** Position in `stageLabels`: converting is part of the first step, so it shares its position with uploading. */
export const stageOrder: Record<StageKey, number> = {
  idle: -1,
  uploading: 0,
  converting: 0,
  analyzing: 1,
  saving: 2,
  complete: 3,
  error: -1,
};

export function getStageProgress(stage: StageKey, uploadProgress = 0, lastActiveStage?: StageKey): number {
  switch (stage) {
    case 'uploading':
      return Math.max(2, Math.min(15, Math.round((uploadProgress / 100) * 15)));
    case 'converting':
      return 20;
    case 'analyzing':
      return 50;
    case 'saving':
      return 75;
    case 'complete':
      return 100;
    case 'error':
      if (lastActiveStage && lastActiveStage !== 'idle' && lastActiveStage !== 'error') {
        return getStageProgress(lastActiveStage, uploadProgress);
      }
      return 0;
    default:
      return 0;
  }
}

/** Whole seconds as `m:ss`, for the time the conversion has been running. */
export function formatElapsed(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

/** What to say under the stages once the console has answered: converted, or stored as it was. */
export function conversionNote(converted: boolean, seconds: number): string {
  return converted
    ? `Converted to H.264 MP4 at the original resolution in ${formatElapsed(seconds)}.`
    : 'Already an H.264 MP4: stored without conversion.';
}

export function getStageAnnouncement(stage: StageKey, uploadProgress = 0, error: string | null = null, convertingSeconds = 0): string {
  switch (stage) {
    case 'uploading':
      return `Stage 1 of 4: Uploading videos${uploadProgress > 0 ? ` (${uploadProgress}%)` : ''}.`;
    case 'converting':
      return `Stage 1 of 4: Uploading videos, converting to H.264 (${formatElapsed(convertingSeconds)}).`;
    case 'analyzing':
      return 'Stage 2 of 4: Understanding the incident.';
    case 'saving':
      return 'Stage 3 of 4: Structuring and saving report.';
    case 'complete':
      return 'Stage 4 of 4: Report ready. Analysis complete.';
    case 'error':
      return `Analysis failed: ${error || 'An error occurred during analysis.'}`;
    default:
      return '';
  }
}
