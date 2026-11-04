// SPDX-License-Identifier: Apache-2.0

import { AppHeader } from '@/sections/shell/components/AppHeader';
import { ReportPage as ReportView } from '@/sections/reports/components/ReportPage';
import { parseStartSeconds } from '@/lib/library/search';

export default async function ReportPage({
  params,
  searchParams,
}: {
  params: Promise<{ videoId: string }>;
  searchParams: Promise<{ revision?: string; t?: string }>;
}) {
  const [{ videoId }, { revision, t }] = await Promise.all([params, searchParams]);

  const parsed = revision === undefined ? null : Number(revision);
  const revisionId = parsed !== null && Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
  const startSeconds = parseStartSeconds(t);

  return (
    <main className="flex h-dvh flex-col overflow-hidden bg-canvas text-ink">
      <div className="shrink-0">
        <AppHeader active="library" subtitle="Report workspace" />
      </div>
      <div className="min-h-0 flex-1">
        <ReportView revisionId={revisionId} startSeconds={startSeconds} videoId={videoId} />
      </div>
    </main>
  );
}
