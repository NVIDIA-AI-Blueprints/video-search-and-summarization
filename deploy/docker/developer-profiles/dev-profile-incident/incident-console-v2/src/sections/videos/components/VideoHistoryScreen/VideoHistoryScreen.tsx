// SPDX-License-Identifier: Apache-2.0

'use client';

import { MutedVideo } from '@/components/muted-video';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { formatTimestamp } from '@/lib/time';
import { ReanalysisCard } from '@/sections/analysis/components/ReanalysisCard';
import { RunHistoryList } from '@/sections/analysis/components/RunHistory';
import { useRunHistory } from '@/sections/analysis/hooks/use-run-history';

/** A video and its analysis history: the video is the parent; runs are its history. */
export function VideoHistoryScreen({ videoId }: { videoId: string }) {
  const { history, error, reload } = useRunHistory(videoId);
  if (error)
    return (
      <div className="mx-auto max-w-3xl px-6 py-16">
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </div>
    );
  if (!history)
    return (
      <div className="mx-auto max-w-7xl px-6 py-16" role="status">
        <Skeleton className="h-64 w-full rounded-4xl" />
      </div>
    );
  const { video } = history;
  return (
    <div className="mx-auto max-w-[1500px] px-4 pb-16 pt-6 sm:px-6 lg:px-12">
      <Card className="gap-0 overflow-hidden rounded-4xl py-0 border border-border bg-card shadow-panel">
        <CardHeader className="border-b border-border bg-muted/30 p-6 sm:p-8">
          <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-primary">Video · analysis history</p>
          <CardTitle className="mt-3 break-all text-2xl font-semibold tracking-tight text-card-foreground sm:text-3xl">
            {video.filename}
          </CardTitle>
          <CardDescription className="mt-2 text-sm text-muted-foreground">
            Uploaded {formatTimestamp(video.uploadedAt)}
            {video.source ? ` · ${video.source}` : ''} · {video.videoId}
          </CardDescription>
          <p className="mt-1 text-xs text-muted-foreground">
            {history.officialRevisionId !== null
              ? 'An official report has been selected for this video.'
              : 'No official report has been selected for this video.'}
          </p>
        </CardHeader>
        <CardContent className="p-0">
          <div className="grid gap-0 lg:grid-cols-[1fr_1fr]">
            <div className="border-b border-border p-6 lg:border-b-0 lg:border-r">
              {video.playbackUrl ? (
                <div className="relative aspect-video w-full min-w-0 min-h-0 overflow-hidden rounded-2xl bg-black flex items-center justify-center">
                  <MutedVideo className="max-h-full max-w-full object-contain" controls src={video.playbackUrl}>
                    <track kind="captions" />
                  </MutedVideo>
                </div>
              ) : (
                <Alert className="rounded-xl border border-dashed border-border p-6 text-sm text-muted-foreground">
                  <AlertDescription>This video has no playable stored object.</AlertDescription>
                </Alert>
              )}
              <h2 className="mt-6 text-sm font-bold uppercase tracking-[0.12em] text-muted-foreground">Analyse again</h2>
              <div className="mt-2">
                <ReanalysisCard onAttempted={() => void reload()} videoId={videoId} />
              </div>
            </div>
            <div className="p-6">
              <h2 className="text-sm font-bold uppercase tracking-[0.12em] text-muted-foreground">Analysis history</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                Every analysis of this video, newest first. Failed attempts are kept here with their reason; they never become reports.
              </p>
              <div className="mt-3">
                <RunHistoryList history={history} onChanged={() => void reload()} />
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
