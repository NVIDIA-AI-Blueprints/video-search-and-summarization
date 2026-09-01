// SPDX-License-Identifier: MIT
/**
 * Image and video renderers for agent markdown.
 *
 * The agent answers with `<img>` / `<video>` pointing at VST media, so these
 * two carry most of what a VSS answer actually looks like.
 */
import { IconDownload, IconExclamationCircle, IconX } from '@tabler/icons-react';
import React, { memo, useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { downloadImageFromUrl } from './download';

/** Placeholder shown while a src is still streaming in as `loading`. */
export const MediaLoading: React.FC<{ label?: string }> = ({ label = 'Loading…' }) => (
  <div className="flex min-h-[200px] items-center justify-center rounded-lg border border-slate-200 bg-slate-50 p-8 dark:border-slate-600 dark:bg-slate-800">
    <div className="text-center">
      <div className="mx-auto h-10 w-10 animate-spin rounded-full border-4 border-gray-200 border-t-[#76b900] dark:border-gray-600 dark:border-t-[#76b900]" />
      <p className="mt-2 text-gray-500 dark:text-gray-400">{label}</p>
    </div>
  </div>
);

interface ImageProps extends React.ImgHTMLAttributes<HTMLImageElement> {
  src: string;
  alt?: string;
  /** Show a download button (agent-emitted images). */
  showDownload?: boolean;
  onDownloadError?: (message: string) => void;
}

export const MarkdownImage = memo(
  ({ src, alt, showDownload = false, onDownloadError, ...props }: ImageProps) => {
    const [error, setError] = useState(false);
    const [isFullscreen, setIsFullscreen] = useState(false);
    const prevSrcRef = useRef(src);

    // Compare through a ref rather than in a dependency array: `src` can be a
    // multi-megabyte data URL and React would diff it on every render.
    useEffect(() => {
      if (prevSrcRef.current !== src) {
        setError(false);
        prevSrcRef.current = src;
      }
    }, [src]);

    const closeFullscreen = useCallback(() => setIsFullscreen(false), []);

    // The overlay is portalled to <body>, so scroll locking and Escape have to
    // be handled here rather than by an ancestor.
    useEffect(() => {
      if (!isFullscreen) return;
      const previousOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      const onKeyDown = (event: KeyboardEvent) => {
        if (event.key === 'Escape') closeFullscreen();
      };
      document.addEventListener('keydown', onKeyDown);
      return () => {
        document.body.style.overflow = previousOverflow;
        document.removeEventListener('keydown', onKeyDown);
      };
    }, [isFullscreen, closeFullscreen]);

    const handleDownload = useCallback(
      async (e: React.MouseEvent) => {
        e.stopPropagation();
        try {
          await downloadImageFromUrl(src, alt);
        } catch (err) {
          onDownloadError?.(err instanceof Error ? err.message : 'Failed to download image');
        }
      },
      [src, alt, onDownloadError],
    );

    if (src === 'loading') return <MediaLoading />;

    if (error) {
      return (
        <span className="inline-flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          <IconExclamationCircle size={16} />
          {alt || 'Image failed to load'}
        </span>
      );
    }

    return (
      <>
        <span className="group relative inline-block max-w-full">
          <img
            src={src}
            alt={alt}
            onError={() => setError(true)}
            onClick={() => setIsFullscreen(true)}
            className="max-w-full cursor-zoom-in rounded-md border border-slate-300 dark:border-slate-600"
            {...props}
          />
          {showDownload && (
            <button
              type="button"
              onClick={handleDownload}
              title="Download image"
              aria-label="Download image"
              className="absolute right-2 top-2 rounded-md bg-black/60 p-1.5 text-white opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
            >
              <IconDownload size={16} />
            </button>
          )}
        </span>

        {isFullscreen &&
          typeof document !== 'undefined' &&
          createPortal(
            <div
              role="dialog"
              aria-modal="true"
              aria-label={alt || 'Image preview'}
              className="fixed inset-0 z-[200] flex items-center justify-center bg-black/90 p-4"
              onClick={closeFullscreen}
            >
              <button
                type="button"
                onClick={closeFullscreen}
                aria-label="Close preview"
                className="absolute right-4 top-4 rounded-md p-2 text-white hover:bg-white/10"
              >
                <IconX size={24} />
              </button>
              <img
                src={src}
                alt={alt}
                className="max-h-full max-w-full object-contain"
                onClick={(e) => e.stopPropagation()}
              />
            </div>,
            document.body,
          )}
      </>
    );
  },
  // Large data URLs make a full string compare expensive; length plus both
  // ends is enough to tell two different frames apart.
  (prev, next) => {
    if (prev.alt !== next.alt) return false;
    const a = prev.src ?? '';
    const b = next.src ?? '';
    if (a.length > 1000 || b.length > 1000) {
      return (
        a.length === b.length &&
        a.slice(0, 100) === b.slice(0, 100) &&
        a.slice(-100) === b.slice(-100)
      );
    }
    return a === b;
  },
);
MarkdownImage.displayName = 'MarkdownImage';

interface VideoProps extends React.VideoHTMLAttributes<HTMLVideoElement> {
  src: string;
}

export const MarkdownVideo = memo(
  ({ src, controls = true, muted = false, ...props }: VideoProps) => {
    if (src === 'loading') return <MediaLoading />;
    return (
      <video
        src={src}
        controls={controls}
        muted={muted}
        autoPlay={false}
        loop={false}
        playsInline
        className="max-w-full rounded-md border border-slate-400 bg-slate-50 shadow-sm dark:border-slate-600 dark:bg-slate-800/50"
        {...props}
      >
        Your browser does not support the video tag.
      </video>
    );
  },
  (prev, next) => prev.src === next.src,
);
MarkdownVideo.displayName = 'MarkdownVideo';
