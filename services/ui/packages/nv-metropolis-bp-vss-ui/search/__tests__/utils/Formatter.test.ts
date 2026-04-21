// SPDX-License-Identifier: MIT
import { formatDatetime, formatTime, formatDateToLocalISO, parseDateAsLocal } from '../../lib-src/utils/Formatter';

describe('formatDatetime', () => {
  it('formats a date correctly', () => {
    const date = new Date(2024, 0, 15, 9, 5, 3); // Jan 15, 2024 09:05:03
    expect(formatDatetime(date)).toBe('Jan 15, 2024 @ 09:05:03');
  });

  it('pads single-digit hours, minutes, and seconds', () => {
    const date = new Date(2024, 5, 1, 1, 2, 3); // Jun 1, 2024 01:02:03
    expect(formatDatetime(date)).toBe('Jun 1, 2024 @ 01:02:03');
  });

  it('handles midnight correctly', () => {
    const date = new Date(2024, 11, 31, 0, 0, 0);
    expect(formatDatetime(date)).toBe('Dec 31, 2024 @ 00:00:00');
  });

  it('handles end of day correctly', () => {
    const date = new Date(2024, 6, 4, 23, 59, 59);
    expect(formatDatetime(date)).toBe('Jul 4, 2024 @ 23:59:59');
  });
});

describe('parseDateAsLocal', () => {
  it('returns null for empty string', () => {
    expect(parseDateAsLocal('')).toBeNull();
  });

  it('returns null for whitespace-only string', () => {
    expect(parseDateAsLocal('   ')).toBeNull();
  });

  it('returns null for non-string input', () => {
    expect(parseDateAsLocal(null as any)).toBeNull();
    expect(parseDateAsLocal(undefined as any)).toBeNull();
    expect(parseDateAsLocal(123 as any)).toBeNull();
  });

  it('returns null for invalid date string', () => {
    expect(parseDateAsLocal('not-a-date')).toBeNull();
  });

  it('parses a date string without timezone info', () => {
    const result = parseDateAsLocal('2024-01-15T09:30:00');
    expect(result).toBeInstanceOf(Date);
    expect(result!.getFullYear()).toBe(2024);
    expect(result!.getMonth()).toBe(0);
    expect(result!.getDate()).toBe(15);
    expect(result!.getHours()).toBe(9);
    expect(result!.getMinutes()).toBe(30);
  });

  it('strips Z timezone suffix and parses as local', () => {
    const result = parseDateAsLocal('2024-06-15T14:30:00Z');
    expect(result).toBeInstanceOf(Date);
    expect(result!.getHours()).toBe(14);
    expect(result!.getMinutes()).toBe(30);
  });

  it('strips +HH:MM timezone offset', () => {
    const result = parseDateAsLocal('2024-06-15T14:30:00+05:30');
    expect(result).toBeInstanceOf(Date);
    expect(result!.getHours()).toBe(14);
    expect(result!.getMinutes()).toBe(30);
  });

  it('strips -HH:MM timezone offset', () => {
    const result = parseDateAsLocal('2024-06-15T14:30:00-08:00');
    expect(result).toBeInstanceOf(Date);
    expect(result!.getHours()).toBe(14);
    expect(result!.getMinutes()).toBe(30);
  });
});

describe('formatTime', () => {
  it('formats time correctly', () => {
    const date = new Date(2024, 0, 1, 14, 30, 45);
    expect(formatTime(date)).toBe('14:30:45');
  });

  it('pads single-digit values', () => {
    const date = new Date(2024, 0, 1, 1, 2, 3);
    expect(formatTime(date)).toBe('01:02:03');
  });

  it('returns placeholder for null', () => {
    expect(formatTime(null)).toBe('--:--:--');
  });

  it('returns placeholder for invalid date', () => {
    expect(formatTime(new Date('invalid'))).toBe('--:--:--');
  });

  it('handles midnight', () => {
    const date = new Date(2024, 0, 1, 0, 0, 0);
    expect(formatTime(date)).toBe('00:00:00');
  });
});

describe('formatDateToLocalISO', () => {
  it('returns null for null input', () => {
    expect(formatDateToLocalISO(null)).toBeNull();
  });

  it('formats date to local ISO string', () => {
    const date = new Date(2024, 0, 15, 9, 5, 3);
    expect(formatDateToLocalISO(date)).toBe('2024-01-15T09:05:03');
  });

  it('pads month, day, hours, minutes, seconds', () => {
    const date = new Date(2024, 2, 5, 1, 2, 3); // March 5
    expect(formatDateToLocalISO(date)).toBe('2024-03-05T01:02:03');
  });

  it('handles last moment of day', () => {
    const date = new Date(2024, 11, 31, 23, 59, 59);
    expect(formatDateToLocalISO(date)).toBe('2024-12-31T23:59:59');
  });

  it('handles first moment of day', () => {
    const date = new Date(2024, 0, 1, 0, 0, 0);
    expect(formatDateToLocalISO(date)).toBe('2024-01-01T00:00:00');
  });
});
