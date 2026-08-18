// SPDX-License-Identifier: MIT
/**
 * Image rendered inside an assistant response.
 *
 * Agents answer with camera snapshots and detection crops, which are usually
 * too small to read inline, so clicking opens a fullscreen view.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import toast from 'react-hot-toast';

import { downloadImageFromUrl } from '../../utils/media/download';

export interface ImageProps {
  src?: string;
  alt?: string;
  /** Shows a download control in the fullscreen view. */
  showDownload?: boolean;
  className?: string;
}

export const Image: React.FC<ImageProps> = ({ src = '', alt = '', showDownload = false, className }) => {
  // Until the image loads there is nothing worth enlarging, and a broken or
  // still-streaming src would open an empty overlay.
  const [hasLoaded, setHasLoaded] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const close = useCallback(() => setIsFullscreen(false), []);

  useEffect(() => {
    if (!isFullscreen) return undefined;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };

    // The overlay covers the viewport; leaving the page scrollable behind it
    // lets a scroll gesture move the transcript underneath.
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', onKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [isFullscreen, close]);

  const handleDownload = useCallback(async () => {
    try {
      await downloadImageFromUrl(src, alt);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not download this image.');
    }
  }, [src, alt]);

  const overlay = isFullscreen ? (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={alt}
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/90"
      onClick={close}
    >
      <div
        className="absolute right-4 top-4 flex gap-2"
        // Controls sit inside the backdrop, which closes on click.
        onClick={(event) => event.stopPropagation()}
      >
        {showDownload && (
          <button
            type="button"
            aria-label="Download image"
            title="Download image"
            onClick={handleDownload}
            className="rounded bg-white/10 px-3 py-2 text-white hover:bg-white/20"
          >
            Download
          </button>
        )}
        <button
          type="button"
          aria-label="Close fullscreen"
          title="Close fullscreen"
          onClick={close}
          className="rounded bg-white/10 px-3 py-2 text-white hover:bg-white/20"
        >
          Close
        </button>
      </div>

      <img
        src={src}
        alt={alt}
        className="max-h-full max-w-full object-contain"
        onClick={(event) => event.stopPropagation()}
      />
    </div>
  ) : null;

  return (
    <>
      <img
        src={src}
        alt={alt}
        className={className}
        onLoad={() => setHasLoaded(true)}
        onClick={() => {
          if (hasLoaded) setIsFullscreen(true);
        }}
        style={{ cursor: hasLoaded ? 'zoom-in' : undefined }}
      />
      {overlay && typeof document !== 'undefined'
        ? createPortal(overlay, document.body)
        : null}
    </>
  );
};
