// SPDX-License-Identifier: MIT
/**
 * Downloading media rendered in chat.
 *
 * Every path ends at `saveAs` with a Blob. Nothing here assigns to
 * `window.location` or opens the URL in the current tab: a failed download must
 * not navigate the user away from a conversation that may still be streaming.
 */
import { saveAs } from 'file-saver';

/** Placeholder `src` used while an image is still being produced. */
const LOADING_PLACEHOLDER = 'loading';

const DATA_URL = /^data:([a-z0-9.+-]+\/[a-z0-9.+-]+)\s*;\s*base64\s*,(.*)$/is;

const EXTENSION_BY_MEDIA_TYPE: Record<string, string> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/jpg': 'jpg',
  'image/gif': 'gif',
  'image/webp': 'webp',
  'image/avif': 'avif',
  'image/svg+xml': 'svg',
};

/**
 * Makes a caption usable as a filename. Chat captions routinely contain
 * timestamps ("Snapshot at 00:05"), and `:` is illegal on Windows and awkward
 * everywhere else.
 */
function toSafeFilename(name: string): string {
  return name
    .replace(/[\\/:*?"<>|]/g, '_')
    .replace(/\s+/g, ' ')
    .trim();
}

function extensionFromUrlPath(url: string): string | null {
  try {
    const { pathname } = new URL(url, 'http://placeholder.invalid');
    const match = /\.([a-z0-9]{1,5})$/i.exec(pathname);
    return match ? match[1].toLowerCase() : null;
  } catch {
    return null;
  }
}

/** Appends `extension` unless `name` already ends with it. */
function withExtension(name: string, extension: string | null): string {
  if (!extension) return name;
  return name.toLowerCase().endsWith(`.${extension.toLowerCase()}`)
    ? name
    : `${name}.${extension}`;
}

function dataUrlToBlob(mediaType: string, base64: string): Blob {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Blob([bytes], { type: mediaType });
}

/**
 * Last resort when `fetch` is refused by CORS: re-request through an <img> with
 * `crossOrigin` set and repaint into a canvas. Only works when the origin sends
 * permissive CORS headers for image loads; rejects otherwise.
 */
function blobViaCanvas(url: string): Promise<Blob> {
  return new Promise<Blob>((resolve, reject) => {
    const image = new Image();
    image.crossOrigin = 'anonymous';

    image.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;

      const context = canvas.getContext('2d');
      if (!context || canvas.width === 0 || canvas.height === 0) {
        reject(new Error('Unable to read image data for download.'));
        return;
      }

      context.drawImage(image, 0, 0);
      canvas.toBlob((blob) => {
        if (blob) resolve(blob);
        else reject(new Error('Unable to read image data for download.'));
      });
    };

    image.onerror = () => {
      reject(new Error('Unable to download this image.'));
    };

    image.src = url;
  });
}

/**
 * Downloads the image at `src`, naming the file after `filename` when given.
 *
 * @throws if the image is still loading, or if the bytes cannot be retrieved.
 */
export async function downloadImageFromUrl(
  src: string,
  filename = 'image',
): Promise<void> {
  if (!src || src === LOADING_PLACEHOLDER) {
    throw new Error('Image is not ready to download yet.');
  }

  const safeName = toSafeFilename(filename) || 'image';

  const dataMatch = DATA_URL.exec(src);
  if (dataMatch) {
    const [, mediaType, base64] = dataMatch;
    const blob = dataUrlToBlob(mediaType, base64);
    saveAs(blob, withExtension(safeName, EXTENSION_BY_MEDIA_TYPE[mediaType.toLowerCase()] ?? 'png'));
    return;
  }

  const extension = extensionFromUrlPath(src);

  try {
    const response = await fetch(src);
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }
    const blob = await response.blob();
    saveAs(blob, withExtension(safeName, extension));
    return;
  } catch {
    // Falls through to the canvas path. A CORS-refused fetch is the common
    // case and is recoverable when the origin allows image loads.
  }

  const blob = await blobViaCanvas(src);
  saveAs(blob, withExtension(safeName, extension ?? 'png'));
}
