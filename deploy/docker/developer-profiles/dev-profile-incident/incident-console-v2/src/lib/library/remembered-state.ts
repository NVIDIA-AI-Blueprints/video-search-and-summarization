// SPDX-License-Identifier: Apache-2.0

// The Library remembers its filters, page and scroll position when a report is
// opened, so Back returns to the same spot. Only Back should use it: clicking the
// Library tab is a fresh visit and must open page 1 with the default filters.

export const LIBRARY_STATE_KEY = 'report-library-state';

export function forgetLibraryState(storage: Pick<Storage, 'removeItem'> | undefined = globalThis.sessionStorage): void {
  try {
    storage?.removeItem(LIBRARY_STATE_KEY);
  } catch {
    /* storage unavailable */
  }
}
