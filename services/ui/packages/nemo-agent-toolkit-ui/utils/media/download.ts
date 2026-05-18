import { saveAs } from 'file-saver';

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

/** Try to read image bytes via fetch (works when URL is same-origin or CORS allows). */
async function blobFromHttpUrl(url: string): Promise<Blob | null> {
  const attempts = [
    () =>
      fetch(url, {
        mode: 'cors',
        credentials: 'omit',
        cache: 'no-cache',
        redirect: 'follow',
      }),
    () =>
      fetch(url, {
        mode: 'cors',
        credentials: 'include',
        cache: 'no-cache',
        redirect: 'follow',
      }),
  ];

  for (const attempt of attempts) {
    try {
      const response = await attempt();
      if (!response.ok) continue;
      const blob = await response.blob();
      if (blob.size > 0) {
        return blob;
      }
    } catch {
      // try next
    }
  }
  return null;
}

/**
 * When fetch() cannot read bytes (opaque / no ACAO but image decode is allowed),
 * redraw into a canvas. Requires server CORS headers for cross-origin images,
 * matching <img crossOrigin="anonymous">.
 */
function blobFromCrossOriginAwareImageDecode(url: string): Promise<Blob | null> {
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    const timer = window.setTimeout(() => resolve(null), 45_000);
    img.onload = () => {
      window.clearTimeout(timer);
      try {
        const w = img.naturalWidth;
        const h = img.naturalHeight;
        if (!w || !h) {
          resolve(null);
          return;
        }
        const canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext('2d');
        if (!ctx) {
          resolve(null);
          return;
        }
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

function suggestedExtensionFromBlob(blob: Blob, src: string): string {
  const fromMime = mimeToExt(blob.type);
  if (fromMime) return fromMime;
  return /^https?:\/\//i.test(src) ? extensionFromUrl(src) : 'png';
}

/**
 * Downloads an image from a URL or data URL — always uses Blob + saveAs so the
 * browser saves a file instead of navigating away (anonymous <a download> ignores
 * filename for cross-origin hrefs).
 */
export async function downloadImageFromUrl(
  src: string,
  filename?: string,
): Promise<void> {
  if (!src || src === 'loading') {
    throw new Error('Image is not ready to download');
  }

  const safeName = sanitizeFilename(filename || 'image');
  let blob: Blob | null = null;

  if (src.startsWith('data:')) {
    const response = await fetch(src);
    blob = await response.blob();
    if (!blob || blob.size === 0) {
      throw new Error('Could not read image data');
    }
    const ext = mimeToExt(blob.type) ?? extensionFromDataUrl(src);
    saveAs(blob, `${safeName}.${ext}`);
    return;
  }

  blob = await blobFromHttpUrl(src);
  let usedCanvasFallback = false;
  if (!blob) {
    blob = await blobFromCrossOriginAwareImageDecode(src);
    usedCanvasFallback = !!blob;
  }

  if (!blob || blob.size === 0) {
    throw new Error(
      'Unable to save this image (browser blocked access). Ask your admin for CORS on media URLs.',
    );
  }

  let ext = suggestedExtensionFromBlob(blob, src);
  if (usedCanvasFallback && ext !== 'png') {
    ext = 'png';
  }

  saveAs(blob, `${safeName}.${ext}`);
}
