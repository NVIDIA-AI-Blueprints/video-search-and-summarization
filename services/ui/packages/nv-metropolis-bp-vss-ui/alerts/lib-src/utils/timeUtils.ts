// SPDX-License-Identifier: MIT

/**
 * Time utility functions for formatting and parsing time windows
 * 
 * This module provides comprehensive utilities for handling time-related operations
 * in the alerts system, including time window formatting, parsing user input,
 * and managing time-based configurations.
 * 
 */

/**
 * Result interface for time parsing operations
 * 
 */
export interface TimeParseResult {
  minutes: number;
  error: string;
}

/**
 * Time unit conversion constants (in minutes)
 */
const TIME_CONVERSIONS = {
  y: 525600,  // years: 365 * 24 * 60
  M: 43200,   // months: 30 * 24 * 60
  w: 10080,   // weeks: 7 * 24 * 60
  d: 1440,    // days: 24 * 60
  h: 60,      // hours
  m: 1,       // minutes
} as const;

/**
 * Formats a time duration in minutes to a human-readable string format
 * Supports: minutes (m), hours (h), days (d), weeks (w), months (M), years (y)
 * 
 * @param minutes - Time duration in minutes
 * @returns Formatted string (e.g., "10m", "2h", "3d", "1w", "2M", "1y")
 * 
 * @example
 * formatTimeWindow(10)      // "10m"
 * formatTimeWindow(120)     // "2h"
 * formatTimeWindow(1440)    // "1d"
 * formatTimeWindow(10080)   // "1w"
 * formatTimeWindow(43200)   // "1M"
 * formatTimeWindow(525600)  // "1y"
 */
export const formatTimeWindow = (minutes: number): string => {
  const parts: string[] = [];
  let remaining = minutes;
  
  // Break down from largest to smallest unit
  const years = Math.floor(remaining / TIME_CONVERSIONS.y);
  if (years > 0) {
    parts.push(`${years}y`);
    remaining %= TIME_CONVERSIONS.y;
  }
  
  const months = Math.floor(remaining / TIME_CONVERSIONS.M);
  if (months > 0) {
    parts.push(`${months}M`);
    remaining %= TIME_CONVERSIONS.M;
  }
  
  const weeks = Math.floor(remaining / TIME_CONVERSIONS.w);
  if (weeks > 0) {
    parts.push(`${weeks}w`);
    remaining %= TIME_CONVERSIONS.w;
  }
  
  const days = Math.floor(remaining / TIME_CONVERSIONS.d);
  if (days > 0) {
    parts.push(`${days}d`);
    remaining %= TIME_CONVERSIONS.d;
  }
  
  const hours = Math.floor(remaining / TIME_CONVERSIONS.h);
  if (hours > 0) {
    parts.push(`${hours}h`);
    remaining %= TIME_CONVERSIONS.h;
  }
  
  if (remaining > 0) {
    parts.push(`${remaining}m`);
  }
  
  return parts.length > 0 ? parts.join(' ') : '0m';
};

/**
 * Verify that matched tokens (with optional whitespace between them) fully cover the input.
 * Rejects inputs with stray characters that don't form valid time tokens.
 */
function tokensFullyCoverInput(trimmed: string, tokens: RegExpMatchArray[]): boolean {
  let position = 0;
  for (const token of tokens) {
    while (position < trimmed.length && (trimmed[position] === ' ' || trimmed[position] === '\t')) {
      position++;
    }
    if (token.index == null || token.index !== position) {
      return false;
    }
    position += token[0].length;
  }
  return position === trimmed.length;
}

const UNIT_ORDER: Record<string, number> = { y: 0, M: 1, w: 2, d: 3, h: 4, m: 5 };

/**
 * Validate that units appear in descending order (y > M > w > d > h > m).
 * Also rejects duplicate units.
 */
function hasValidUnitOrder(tokens: RegExpMatchArray[]): boolean {
  for (let i = 1; i < tokens.length; i++) {
    const previousUnit = tokens[i - 1][0].slice(-1);
    const currentUnit = tokens[i][0].slice(-1);
    if (UNIT_ORDER[previousUnit] >= UNIT_ORDER[currentUnit]) {
      return false;
    }
  }
  return true;
}

/**
 * Core function to parse time string to minutes
 * Supports: minutes (m), hours (h), days (d), weeks (w), months (M), years (y)
 * 
 * @param input - Time string (e.g., "40m", "2h", "1w 2d")
 * @returns Object with minutes and error (if any)
 */
function parseTimeString(input: string): TimeParseResult {
  const trimmed = input.trim();
  
  if (!trimmed) {
    return { minutes: 0, error: 'Please enter a time value' };
  }

  if (/[YWDH]/.test(trimmed)) {
    return { minutes: 0, error: 'Invalid format. Use lowercase letters (y, w, d, h, m) except M for months' };
  }

  // Cap input length to prevent quadratic regex cost on adversarial strings
  if (trimmed.length > 50) {
    return { minutes: 0, error: 'Input is too long' };
  }

  // \d+ and [yMwdhm] are disjoint char classes — no exponential backtracking possible.
  // Worst case is O(n²) on digit-only strings, mitigated by the length cap above.
  const tokens = Array.from(trimmed.matchAll(/\d+[yMwdhm]/g));
  const invalidFormatError = 'Invalid format. Use: 40m, 4h, 1d, 1w, 1M, 1y or combinations like 1h 30m or 1h30m';

  if (tokens.length === 0) {
    return { minutes: 0, error: invalidFormatError };
  }

  if (!tokensFullyCoverInput(trimmed, tokens)) {
    return { minutes: 0, error: invalidFormatError };
  }

  if (!hasValidUnitOrder(tokens)) {
    return { minutes: 0, error: 'Units must be in descending order (e.g., 1y 2M or 1h 30m, not 1m 2h)' };
  }

  let totalMinutes = 0;
  for (const token of tokens) {
    const value = Number.parseInt(token[0], 10);
    const unit = token[0].slice(-1) as keyof typeof TIME_CONVERSIONS;
    totalMinutes += value * TIME_CONVERSIONS[unit];
  }

  if (totalMinutes === 0) {
    return { minutes: 0, error: 'Time must be greater than 0' };
  }

  return { minutes: totalMinutes, error: '' };
}

/**
 * Parses user input time strings into minutes with comprehensive validation
 * Supports: minutes (m), hours (h), days (d), weeks (w), months (M), years (y)
 * Note: Max limit validation is handled separately by maxSearchTimeLimit
 * 
 * @example
 * parseTimeInput("40m")     // { minutes: 40, error: '' }
 * parseTimeInput("2h")      // { minutes: 120, error: '' }
 * parseTimeInput("1h 30m")  // { minutes: 90, error: '' }
 * parseTimeInput("1d")      // { minutes: 1440, error: '' }
 * parseTimeInput("1w")      // { minutes: 10080, error: '' }
 * parseTimeInput("1M")      // { minutes: 43200, error: '' }
 * parseTimeInput("1y")      // { minutes: 525600, error: '' }
 * parseTimeInput("1w 2d")   // { minutes: 12960, error: '' }
 */
export const parseTimeInput = (input: string): TimeParseResult => {
  return parseTimeString(input);
};

/**
 * Predefined time window options for the dropdown selector
 * 
 */
export const TIME_WINDOW_OPTIONS = [
  { label: '10m', value: 10 },
  { label: '20m', value: 20 },
  { label: '30m', value: 30 },
  { label: '1h', value: 60 },
  { label: '2h', value: 120 },
  { label: 'Custom', value: -1 }
] as const;

/**
 * Determines the appropriate display label for a given time window value
 * 
 */
export const getCurrentTimeWindowLabel = (timeWindow: number): string => {
  const option = TIME_WINDOW_OPTIONS.find(opt => opt.value === timeWindow);
  if (option && option.value !== -1) {
    return option.label;
  }
  return formatTimeWindow(timeWindow);
};

/**
 * Parse time limit string to minutes
 * Supports: 0 (unlimited), 10m (minutes), 2h (hours), 3d (days), 1w (weeks), 2M (months), 1y (years)
 * 
 * @param timeLimitStr - Time limit string (e.g., "10m", "2d", "1w", "0")
 * @returns Number of minutes, or 0 for unlimited/invalid
 * 
 * @example
 * parseTimeLimit("10m")  // 10
 * parseTimeLimit("2h")   // 120
 * parseTimeLimit("1d")   // 1440
 * parseTimeLimit("1w")   // 10080
 * parseTimeLimit("1M")   // 43200
 * parseTimeLimit("1y")   // 525600
 * parseTimeLimit("0")    // 0 (unlimited)
 */
export const parseTimeLimit = (timeLimitStr: string | undefined): number => {
  if (!timeLimitStr || timeLimitStr === '0') return 0; // 0 = unlimited
  
  // Use the shared parsing logic
  const result = parseTimeString(timeLimitStr);
  
  // Return 0 (unlimited) if invalid format
  if (result.error) {
    return 0;
  }
  
  return result.minutes;
};

/**
 * Time format for alert timestamps: local (browser timezone) or UTC
 */
export type AlertTimeFormat = 'local' | 'utc';

const LOCALE_OPTS = {
  date: { month: '2-digit' as const, day: '2-digit' as const, year: 'numeric' as const },
  time: { hour: '2-digit' as const, minute: '2-digit' as const, second: '2-digit' as const, hour12: true as const },
};

/**
 * Format an alert timestamp for display in either local time or UTC.
 *
 * @param timestamp - ISO timestamp string or number (ms)
 * @param useUtc - if true, format in UTC; otherwise use browser local time
 * @returns Formatted date/time string or original value on parse error
 */
export const formatAlertTimestamp = (timestamp: string | number, useUtc: boolean): string => {
  try {
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return String(timestamp);
    const dateStr = date.toLocaleDateString('en-US', useUtc ? { ...LOCALE_OPTS.date, timeZone: 'UTC' } : LOCALE_OPTS.date);
    const timeStr = date.toLocaleTimeString('en-US', useUtc ? { ...LOCALE_OPTS.time, timeZone: 'UTC' } : LOCALE_OPTS.time);
    return `${dateStr} ${timeStr}`;
  } catch {
    return String(timestamp);
  }
};
