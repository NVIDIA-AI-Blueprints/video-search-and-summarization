// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/** Keep uploaded files when explicitly enabled for this deployment. Live segments are unaffected. */
export async function protectUploadedFile(
  uploadUrl: string,
  response: { filePath?: unknown },
  abortSignal?: AbortSignal,
): Promise<void> {
  const runtimeFlag = typeof window === 'undefined'
    ? undefined
    : (window as Window & { __ENV?: Record<string, string | undefined> }).__ENV?.NEXT_PUBLIC_PROTECT_UPLOADED_FILES;
  if ((runtimeFlag || process.env.NEXT_PUBLIC_PROTECT_UPLOADED_FILES) !== 'true') return;

  const controller = new AbortController();
  const abort = () => controller.abort();
  const timeout = setTimeout(abort, 10_000);
  abortSignal?.addEventListener('abort', abort, { once: true });
  if (abortSignal?.aborted) controller.abort();

  try {
    const base = uploadUrl.replace(/\/$/, '');
    if (!/\/v1\/storage\/file$/.test(base)) {
      throw new Error('The configured upload endpoint does not support file protection.');
    }
    if (typeof response.filePath !== 'string' || !response.filePath.trim()) {
      throw new Error('The upload response did not include its file path.');
    }

    const protection = await fetch(`${base}/protect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filePath: [response.filePath], protect: true }),
      signal: controller.signal,
    });
    if (!protection.ok) throw new Error(`Protection request returned HTTP ${protection.status}.`);
    const result: unknown = await protection.json();
    if (!result || typeof result !== 'object' ||
        !Array.isArray((result as { invalidFiles?: unknown }).invalidFiles) ||
        (result as { invalidFiles: unknown[] }).invalidFiles.length > 0) {
      throw new Error('VIOS did not accept file protection.');
    }

    const verification = await fetch(`${base}/protected`, {
      cache: 'no-store',
      signal: controller.signal,
    });
    if (!verification.ok) throw new Error(`Protection verification returned HTTP ${verification.status}.`);
    const protectedFiles: unknown = await verification.json();
    if (!Array.isArray(protectedFiles) || !protectedFiles.includes(response.filePath)) {
      throw new Error('The uploaded file is absent from the protected-file list.');
    }
  } catch (error) {
    const detail = controller.signal.aborted
      ? 'The protection check was interrupted or timed out.'
      : error instanceof Error ? error.message : 'The protection check failed.';
    // This runs after chunk retries have finished. Never retry an accepted upload
    // merely because retention protection failed.
    throw new Error(`Upload completed, but retention protection could not be verified. Do not upload again; check this recording's protection. ${detail}`);
  } finally {
    clearTimeout(timeout);
    abortSignal?.removeEventListener('abort', abort);
  }
}
