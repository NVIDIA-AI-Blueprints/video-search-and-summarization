// SPDX-License-Identifier: MIT

/**
 * Rate-limits `func` to once per `limit` ms, running on the leading edge and
 * again on the trailing edge if calls kept arriving.
 *
 * Used for autoscroll during streaming: frames land far faster than the display
 * refreshes, and scrolling per frame is what makes a streaming transcript
 * stutter. The trailing call matters — without it the transcript stops one
 * interval short of the final token.
 */
export function throttle<T extends (...args: any[]) => any>(func: T, limit: number): T {
  let trailingTimer: ReturnType<typeof setTimeout>;
  let lastRan: number;

  return ((...args: any[]) => {
    if (!lastRan) {
      func(...args);
      lastRan = Date.now();
      return;
    }

    clearTimeout(trailingTimer);
    trailingTimer = setTimeout(
      () => {
        if (Date.now() - lastRan >= limit) {
          func(...args);
          lastRan = Date.now();
        }
      },
      limit - (Date.now() - lastRan),
    );
  }) as T;
}
