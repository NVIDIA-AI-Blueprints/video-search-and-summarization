import {
  IconExclamationCircle,
  IconMaximize,
  IconX,
} from '@tabler/icons-react';
import React, { memo, useRef, useState, useCallback, useEffect } from 'react';

interface ImageProps extends React.ImgHTMLAttributes<HTMLImageElement> {
  src: string;
  alt?: string;
}

export const Image = memo(
  ({ src, alt, ...props }: ImageProps) => {
    const imgRef = useRef(null);
    const prevSrcRef = useRef(src);
    const [error, setError] = useState(false);
    const [isFullscreen, setIsFullscreen] = useState(false);
    const [isLoaded, setIsLoaded] = useState(false);
    
    const handleImageError = useCallback(() => {
      console.error(`Image failed to load: ${src}`);
      setError(true);
    }, [src]);

    const handleImageLoad = useCallback(() => {
      setIsLoaded(true);
    }, []);

    const toggleFullscreen = useCallback((e: React.MouseEvent) => {
      e.stopPropagation();
      setIsFullscreen((prev) => !prev);
    }, []);

    // Reset error and loaded state when src changes
    // Use ref comparison to avoid comparing large base64 strings repeatedly
    useEffect(() => {
      if (prevSrcRef.current !== src) {
        setError(false);
        setIsLoaded(false);
        prevSrcRef.current = src;
      }
    }, [src]);

    // Early return for loading state
    if (src === 'loading') {
      return (
        <div className="flex items-center justify-center p-8 bg-slate-50 rounded-lg border border-slate-200 min-h-[200px]">
          <div className="text-center">
            <svg
              aria-hidden="true"
              className="w-10 h-10 text-gray-200 animate-spin fill-green-500 mx-auto"
              viewBox="0 0 100 101"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M100 50.5908C100 78.2051 77.6142 100.591 50 100.591C22.3858 100.591 0 78.2051 0 50.5908C0 22.9766 22.3858 0.59082 50 0.59082C77.6142 0.59082 100 22.9766 100 50.5908ZM9.08144 50.5908C9.08144 73.1895 27.4013 91.5094 50 91.5094C72.5987 91.5094 90.9186 73.1895 90.9186 50.5908C90.9186 27.9921 72.5987 9.67226 50 9.67226C27.4013 9.67226 9.08144 27.9921 9.08144 50.5908Z"
                fill="currentColor"
              />
              <path
                d="M93.9676 39.0409C96.393 38.4038 97.8624 35.9116 97.0079 33.5539C95.2932 28.8227 92.871 24.3692 89.8167 20.348C85.8452 15.1192 80.8826 10.7238 75.2124 7.41289C69.5422 4.10194 63.2754 1.94025 56.7698 1.05124C51.7666 0.367541 46.6976 0.446843 41.7345 1.27873C39.2613 1.69328 37.813 4.19778 38.4501 6.62326C39.0873 9.04874 41.5694 10.4717 44.0505 10.1071C47.8511 9.54855 51.7191 9.52689 55.5402 10.0491C60.8642 10.7766 65.9928 12.5457 70.6331 15.2552C75.2735 17.9648 79.3347 21.5619 82.5849 25.841C84.9175 28.9121 86.7997 32.2913 88.1811 35.8758C89.083 38.2158 91.5421 39.6781 93.9676 39.0409Z"
                fill="currentFill"
              />
            </svg>
            <p className="mt-3 text-sm text-gray-600">Loading...</p>
          </div>
        </div>
      );
    }

    return (
      <span className="relative block my-4">
        {error ? (
          <span className="inline-flex items-center justify-center p-4 bg-slate-50 rounded-lg border border-slate-200">
            <IconExclamationCircle className="w-5 h-5 text-red-500 mr-2" />
            <span className="text-red-600 text-sm">
              Failed to load image with src:{' '}
              {src.slice(0, 50) + (src.length > 50 ? '...' : '')}
            </span>
          </span>
        ) : (
          <span className="relative block w-full">
            {/* Image - always rendered to allow lazy loading to work */}
            {/* Use opacity instead of display:none so browser can still detect it for lazy loading */}
            <img
              ref={imgRef}
              src={src}
              alt={alt || 'image'}
              onError={handleImageError}
              onLoad={handleImageLoad}
              loading="eager"  // Changed from lazy - lazy + hidden causes loading issues
              decoding="async"
              className="object-cover rounded-lg border border-slate-100 shadow-xs cursor-pointer"
              onClick={toggleFullscreen}
              style={{ 
                maxWidth: '100%', 
                height: 'auto',
                opacity: isLoaded ? 1 : 0,
                position: isLoaded ? 'relative' : 'absolute',
                // When not loaded, position absolutely so it doesn't take space
                // but is still in DOM for browser to load it
                top: 0,
                left: 0,
              }}
              {...props}
            />
            {/* Loading indicator while image loads - shown behind/instead of image */}
            {!isLoaded && !error && (
              <div className="flex items-center justify-center p-8 bg-slate-50 rounded-lg border border-slate-200 min-h-[200px]">
                <div className="text-center">
                  <svg
                    aria-hidden="true"
                    className="w-10 h-10 text-gray-200 animate-spin fill-green-500 mx-auto"
                    viewBox="0 0 100 101"
                    fill="none"
                    xmlns="http://www.w3.org/2000/svg"
                  >
                    <path
                      d="M100 50.5908C100 78.2051 77.6142 100.591 50 100.591C22.3858 100.591 0 78.2051 0 50.5908C0 22.9766 22.3858 0.59082 50 0.59082C77.6142 0.59082 100 22.9766 100 50.5908ZM9.08144 50.5908C9.08144 73.1895 27.4013 91.5094 50 91.5094C72.5987 91.5094 90.9186 73.1895 90.9186 50.5908C90.9186 27.9921 72.5987 9.67226 50 9.67226C27.4013 9.67226 9.08144 27.9921 9.08144 50.5908Z"
                      fill="currentColor"
                    />
                    <path
                      d="M93.9676 39.0409C96.393 38.4038 97.8624 35.9116 97.0079 33.5539C95.2932 28.8227 92.871 24.3692 89.8167 20.348C85.8452 15.1192 80.8826 10.7238 75.2124 7.41289C69.5422 4.10194 63.2754 1.94025 56.7698 1.05124C51.7666 0.367541 46.6976 0.446843 41.7345 1.27873C39.2613 1.69328 37.813 4.19778 38.4501 6.62326C39.0873 9.04874 41.5694 10.4717 44.0505 10.1071C47.8511 9.54855 51.7191 9.52689 55.5402 10.0491C60.8642 10.7766 65.9928 12.5457 70.6331 15.2552C75.2735 17.9648 79.3347 21.5619 82.5849 25.841C84.9175 28.9121 86.7997 32.2913 88.1811 35.8758C89.083 38.2158 91.5421 39.6781 93.9676 39.0409Z"
                      fill="currentFill"
                    />
                  </svg>
                  <p className="mt-3 text-sm text-gray-600">Loading image...</p>
                </div>
              </div>
            )}
            {/* Fullscreen Mode - this is fine as it's positioned fixed outside normal flow */}
            {isFullscreen && !error && isLoaded && (
              <div
                className="fixed inset-0 bg-black/95 flex items-center justify-center z-50"
                onClick={toggleFullscreen}
                onKeyDown={(e) => e.key === 'Escape' && toggleFullscreen(e as any)}
                role="dialog"
                aria-modal="true"
                tabIndex={-1}
              >
                <div className="relative max-w-[90vw] max-h-[90vh]">
                  <img
                    src={src}
                    alt={alt || 'image'}
                    decoding="async"
                    className="max-w-full max-h-full object-contain rounded-lg"
                    style={{ maxWidth: '90vw', maxHeight: '90vh' }}
                  />
                </div>
              </div>
            )}
          </span>
        )}
      </span>
    );
  },
  (prevProps: ImageProps, nextProps: ImageProps) => {
    // Fast comparison for small strings
    if (prevProps.src.length < 1000) {
      return prevProps.src === nextProps.src && prevProps.alt === nextProps.alt;
    }
    
    // For large strings (likely base64 images), use optimized comparison
    // Check length first (fast), then compare first and last chunks
    if (prevProps.src.length !== nextProps.src.length) {
      return false;
    }
    
    // Compare first 100 and last 100 characters (much faster than full comparison)
    const prevStart = prevProps.src.substring(0, 100);
    const prevEnd = prevProps.src.substring(prevProps.src.length - 100);
    const nextStart = nextProps.src.substring(0, 100);
    const nextEnd = nextProps.src.substring(nextProps.src.length - 100);
    
    return prevStart === nextStart && 
           prevEnd === nextEnd && 
           prevProps.alt === nextProps.alt;
  }
);
