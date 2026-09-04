// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
/**
 * Image download for agent-rendered media.
 *
 * Always goes through a Blob rather than `<a download>`: an anonymous download
 * attribute is ignored for cross-origin hrefs, so the browser navigates away
 * from the app instead of saving. VSS media comes from VST on another origin,
 * which is exactly that case.
 *
 * Ported from the toolkit's `utils/media/download.ts`.
 */
/**
 * Save a Blob under a chosen filename.
 *
 * Written out rather than pulling in `file-saver` (which ships no types) —
 * every browser this app supports honours `download` for a blob: URL, which is
 * the only case that reaches here.
 */
function saveAs(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoking synchronously can cancel the download in Safari.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function sanitizeFilename(name: string): string {
  return name.replace(/[^\w\s.-]/g, '_').trim().slice(0, 100) || 'image';
}

function extensionFromDataUrl(src: string): string {
  const match = /^data:image\/(\w+)/.exec(src);
  if (!match) return 'png';
  return match[1] === 'jpeg' ? 'jpg' : match[1];
}

function extensionFromUrl(src: string): string {
  const pathname = src.split('?')[0]?.split('#')[0] ?? '';
  const ext = pathname.split('.').pop()?.toLowerCase();
  if (ext && ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'].includes(ext)) {
    return ext === 'jpeg' ? 'jpg' : ext;
  }
  return 'jpg';
}

function mimeToExt(mime: string): string | null {
  if (!mime || typeof mime !== 'string') return null;
  if (mime.includes('jpeg')) return 'jpg';
  if (mime.includes('png')) return 'png';
  if (mime.includes('gif')) return 'gif';
  if (mime.includes('webp')) return 'webp';
  if (mime.includes('bmp')) return 'bmp';
  return null;
}

/** Read bytes directly. Works same-origin, or cross-origin when CORS allows. */
async function blobFromHttpUrl(url: string): Promise<Blob | null> {
  // Two attempts because a proxy may require cookies while a public bucket
  // rejects them; neither ordering works for both.
  const attempts: RequestInit[] = [
    { mode: 'cors', credentials: 'omit', cache: 'no-cache', redirect: 'follow' },
    { mode: 'cors', credentials: 'include', cache: 'no-cache', redirect: 'follow' },
  ];

  for (const init of attempts) {
    try {
      const response = await fetch(url, init);
      if (!response.ok) continue;
      const blob = await response.blob();
      if (blob.size > 0) return blob;
    } catch {
      // Try the next credential mode.
    }
  }
  return null;
}

/**
 * Fallback for when fetch cannot read bytes but the image still decodes:
 * redraw it into a canvas. Needs the same CORS headers `<img crossOrigin>` does.
 */
function blobFromImageDecode(url: string): Promise<Blob | null> {
  return new Promise((resolve) => {
    const img = new window.Image();
    img.crossOrigin = 'anonymous';
    const timer = window.setTimeout(() => resolve(null), 45_000);
    img.onload = () => {
      window.clearTimeout(timer);
      try {
        const { naturalWidth: w, naturalHeight: h } = img;
        if (!w || !h) return resolve(null);
        const canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext('2d');
        if (!ctx) return resolve(null);
        ctx.drawImage(img, 0, 0);
        canvas.toBlob((b) => resolve(b ?? null), 'image/png', 0.92);
      } catch {
        resolve(null);
      }
    };
    img.onerror = () => {
      window.clearTimeout(timer);
      resolve(null);
    };
    img.src = url;
  });
}

export async function downloadImageFromUrl(src: string, filename?: string): Promise<void> {
  if (!src || src === 'loading') throw new Error('Image is not ready to download');

  const safeName = sanitizeFilename(filename || 'image');

  if (src.startsWith('data:')) {
    const blob = await (await fetch(src)).blob();
    if (!blob?.size) throw new Error('Could not read image data');
    saveAs(blob, `${safeName}.${mimeToExt(blob.type) ?? extensionFromDataUrl(src)}`);
    return;
  }

  let blob = await blobFromHttpUrl(src);
  let usedCanvas = false;
  if (!blob) {
    blob = await blobFromImageDecode(src);
    usedCanvas = !!blob;
  }
  if (!blob?.size) {
    throw new Error(
      'Unable to save this image (browser blocked access). Ask your admin for CORS on media URLs.',
    );
  }

  // The canvas path always re-encodes to PNG, whatever the source was.
  const ext = usedCanvas
    ? 'png'
    : (mimeToExt(blob.type) ?? (/^https?:\/\//i.test(src) ? extensionFromUrl(src) : 'png'));
  saveAs(blob, `${safeName}.${ext}`);
}
