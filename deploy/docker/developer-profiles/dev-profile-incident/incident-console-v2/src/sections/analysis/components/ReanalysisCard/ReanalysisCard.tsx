// SPDX-License-Identifier: Apache-2.0

'use client';

import { RotateCw } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { describeCode } from '@/modules/videos/domain/run-labels';

const MAX_PROMPT = 1000;

interface ModelOption {
  id: string;
  label: string;
}

export function useReanalysis({ videoId, onAttempted }: { videoId: string; onAttempted?: () => void }) {
  const router = useRouter();
  const [models, setModels] = useState<ModelOption[]>([]);
  const [model, setModel] = useState('');
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<{ tone: 'error' | 'ok'; lines: string[] } | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let active = true;
    void fetch('/api/models', { cache: 'no-store' })
      .then((response) => response.json() as Promise<{ models?: ModelOption[]; defaultModel?: string }>)
      .then((payload) => {
        if (!active) return;
        setModels(payload.models || []);
        setModel(payload.defaultModel || payload.models?.[0]?.id || '');
      })
      .catch(() => undefined);
    return () => {
      active = false;
      if (timer.current) window.clearInterval(timer.current);
    };
  }, []);

  async function run(onSuccess?: () => void) {
    if (busy || !model) return;
    setBusy(true);
    setResult(null);
    setElapsed(0);
    const started = Date.now();
    timer.current = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    try {
      const response = await fetch(`/api/videos/${encodeURIComponent(videoId)}/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model, ...(prompt.trim() ? { additionalInstruction: prompt } : {}) }),
      });
      const payload = (await response.json()) as {
        run?: { videoId: string; revisionId: number };
        error?: string;
        outcome?: string;
        code?: string;
        codes?: string[];
      };
      if (response.ok && payload.run) {
        onSuccess?.();
        router.push(`/reports/${encodeURIComponent(payload.run.videoId)}?revision=${payload.run.revisionId}`);
        return;
      }
      const lines =
        payload.outcome === 'contract_failed'
          ? [
              'The model answered, but the answer broke the incident contract, so no report was created. The attempt is recorded in the history.',
              ...(payload.codes || []).map(describeCode),
            ]
          : payload.outcome === 'request_failed'
            ? [
                'The model request did not produce a usable answer. The attempt is recorded in the history.',
                describeCode(payload.code || ''),
              ]
            : [payload.error || `Re-analysis failed (HTTP ${response.status})`];
      setResult({ tone: 'error', lines });
      onAttempted?.();
    } catch (cause) {
      setResult({ tone: 'error', lines: [cause instanceof Error ? cause.message : 'Re-analysis failed'] });
    } finally {
      if (timer.current) window.clearInterval(timer.current);
      setBusy(false);
    }
  }

  return {
    models,
    model,
    setModel,
    prompt,
    setPrompt,
    busy,
    elapsed,
    result,
    setResult,
    run,
  };
}

/**
 * Analyse the already-stored video again (a new AI version) with one of the
 * approved models and an optional reviewer prompt typed here. Every attempt is
 * a new run; earlier versions and the reviewer's edits are not changed or
 * carried across, and the new version is not made official.
 */
export function ReanalysisCard({ videoId, onAttempted }: { videoId: string; onAttempted?: () => void }) {
  const { models, model, setModel, prompt, setPrompt, busy, elapsed, result, run } = useReanalysis({ videoId, onAttempted });

  return (
    <Card data-testid="reanalysis-card" className="border-border bg-card">
      <CardHeader className="pb-3">
        <CardDescription className="text-xs text-muted-foreground">
          Runs the stored video again as a new AI version. Earlier versions and reviewer edits are kept as they are and are not used by the
          new run; the new version is not made official automatically.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 pt-0">
        <div>
          <label className="block text-xs font-bold text-foreground" htmlFor="reanalysis-model">
            Model
          </label>
          <div className="mt-1">
            <Select disabled={busy} onValueChange={(val) => setModel(val)} value={model}>
              <SelectTrigger id="reanalysis-model" className="w-full">
                <SelectValue placeholder="Select model" />
              </SelectTrigger>
              <SelectContent>
                {models.map((option) => (
                  <SelectItem key={option.id} value={option.id}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <div>
          <label className="block text-xs font-bold text-foreground" htmlFor="reviewer-prompt">
            Reviewer prompt (optional)
          </label>
          <Textarea
            id="reviewer-prompt"
            className="mt-1 min-h-[80px]"
            disabled={busy}
            maxLength={MAX_PROMPT}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="e.g. Pay particular attention to the person entering from the left side of the frame."
            value={prompt}
          />
          <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
            <span>Adds context for this run; it can’t change the report format.</span>
            <span>
              {prompt.length}/{MAX_PROMPT}
            </span>
          </div>
        </div>
        <div>
          <Button size="sm" className="rounded-full" disabled={busy || !model} onClick={() => void run()} type="button">
            {busy ? `Analysing… ${elapsed}s` : 'Generate new version'}
          </Button>
        </div>
        {result && (
          <Alert variant={result.tone === 'error' ? 'destructive' : 'default'} aria-live="polite" className="mt-3 text-xs">
            <AlertDescription className="space-y-1">
              {result.lines.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Compact dialog-based action for the report sticky top bar.
 */
export function ReanalysisDialog({ videoId, onAttempted }: { videoId: string; onAttempted?: () => void }) {
  const [open, setOpen] = useState(false);
  const { models, model, setModel, prompt, setPrompt, busy, elapsed, result, setResult, run } = useReanalysis({
    videoId,
    onAttempted,
  });

  function handleOpenChange(next: boolean) {
    if (busy) return;
    setOpen(next);
    if (next) setResult(null);
  }

  return (
    <Dialog onOpenChange={handleOpenChange} open={open}>
      <DialogTrigger asChild>
        <Button
          aria-label="Re-analyse this incident"
          className="border-primary/20 bg-primary/5 text-xs font-semibold hover:bg-primary/10"
          data-testid="reanalysis-trigger"
          size="sm"
          type="button"
          variant="outline"
        >
          <RotateCw aria-hidden="true" data-icon="inline-start" />
          <span className="max-sm:sr-only sm:ml-1.5">Re-analyse</span>
        </Button>
      </DialogTrigger>
      <DialogContent className="gap-0 overflow-hidden p-0 sm:max-w-lg" data-testid="reanalysis-dialog">
        <DialogHeader className="border-b border-border/70 bg-muted/40 px-6 py-5 pr-12">
          <DialogTitle className="text-lg font-bold tracking-tight text-foreground">Re-analyse incident</DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground">
            Runs the stored video again as a new AI version. Earlier versions and reviewer edits are kept as they are and are not used by
            the new run; the new version is not made official automatically.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4 p-6">
          <div>
            <label className="block text-xs font-bold text-foreground" htmlFor="dialog-reanalysis-model">
              Model
            </label>
            <div className="mt-1">
              <Select disabled={busy} onValueChange={(val) => setModel(val)} value={model}>
                <SelectTrigger id="dialog-reanalysis-model" className="w-full">
                  <SelectValue placeholder="Select model" />
                </SelectTrigger>
                <SelectContent>
                  {models.map((option) => (
                    <SelectItem key={option.id} value={option.id}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div>
            <label className="block text-xs font-bold text-foreground" htmlFor="dialog-reviewer-prompt">
              Reviewer prompt (optional)
            </label>
            <Textarea
              id="dialog-reviewer-prompt"
              className="mt-1 min-h-[80px]"
              disabled={busy}
              maxLength={MAX_PROMPT}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="e.g. Pay particular attention to the person entering from the left side of the frame."
              value={prompt}
            />
            <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
              <span>Adds context for this run; it can’t change the report format.</span>
              <span>
                {prompt.length}/{MAX_PROMPT}
              </span>
            </div>
          </div>
          {result && (
            <Alert variant={result.tone === 'error' ? 'destructive' : 'default'} aria-live="polite" className="text-xs">
              <AlertDescription className="space-y-1">
                {result.lines.map((line) => (
                  <p key={line}>{line}</p>
                ))}
              </AlertDescription>
            </Alert>
          )}
        </div>
        <DialogFooter className="gap-2 border-t border-border/70 bg-muted/20 px-6 py-4 sm:gap-0">
          <Button disabled={busy} onClick={() => setOpen(false)} type="button" variant="outline">
            Cancel
          </Button>
          <Button disabled={busy || !model} onClick={() => void run(() => setOpen(false))} type="button">
            {busy ? `Analysing… ${elapsed}s` : 'Generate new version'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
