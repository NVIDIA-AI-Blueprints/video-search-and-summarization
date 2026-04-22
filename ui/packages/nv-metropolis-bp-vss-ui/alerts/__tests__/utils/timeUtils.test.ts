// SPDX-License-Identifier: MIT
import {
  formatTimeWindow,
  parseTimeInput,
  parseTimeLimit,
  formatAlertTimestamp,
  getCurrentTimeWindowLabel,
  TIME_WINDOW_OPTIONS,
} from '../../lib-src/utils/timeUtils';

describe('formatTimeWindow', () => {
  it.each([
    [10, '10m'], [45, '45m'],
    [60, '1h'], [120, '2h'],
    [1440, '1d'], [2880, '2d'],
    [10080, '1w'],
    [43200, '1M'],
    [525600, '1y'],
  ])('formats %i minutes as %s', (input, expected) => {
    expect(formatTimeWindow(input)).toBe(expected);
  });

  it.each([
    [90, '1h 30m'],
    [1500, '1d 1h'],
    [11520, '1w 1d'],
  ])('formats combined %i minutes as %s', (input, expected) => {
    expect(formatTimeWindow(input)).toBe(expected);
  });

  it('returns 0m for zero', () => {
    expect(formatTimeWindow(0)).toBe('0m');
  });
});

describe('parseTimeInput', () => {
  describe('valid inputs', () => {
    it.each([
      ['40m', 40],
      ['2h', 120],
      ['1d', 1440],
      ['1w', 10080],
      ['1M', 43200],
      ['1y', 525600],
      ['1h 30m', 90],
      ['1h30m', 90],
      ['1w 2d', 12960],
    ])('parses "%s" to %i minutes', (input, expectedMinutes) => {
      expect(parseTimeInput(input)).toEqual({ minutes: expectedMinutes, error: '' });
    });
  });

  describe('invalid inputs', () => {
    it('rejects empty string', () => {
      const result = parseTimeInput('');
      expect(result.error).toBeTruthy();
      expect(result.minutes).toBe(0);
    });

    it('rejects whitespace-only', () => {
      const result = parseTimeInput('   ');
      expect(result.error).toBeTruthy();
    });

    it('rejects plain numbers without units', () => {
      const result = parseTimeInput('40');
      expect(result.error).toBeTruthy();
    });

    it('rejects invalid characters', () => {
      const result = parseTimeInput('abc');
      expect(result.error).toBeTruthy();
    });

    it('rejects uppercase units (except M)', () => {
      expect(parseTimeInput('2H').error).toBeTruthy();
      expect(parseTimeInput('1D').error).toBeTruthy();
      expect(parseTimeInput('1W').error).toBeTruthy();
    });

    it('rejects wrong unit order (ascending instead of descending)', () => {
      const result = parseTimeInput('1m 2h');
      expect(result.error).toContain('descending order');
    });

    it('rejects duplicate units', () => {
      const result = parseTimeInput('1h 2h');
      expect(result.error).toBeTruthy();
    });

    it('rejects zero values', () => {
      const result = parseTimeInput('0m');
      expect(result.error).toContain('greater than 0');
    });
  });
});

describe('parseTimeLimit', () => {
  it('returns 0 for undefined', () => {
    expect(parseTimeLimit(undefined)).toBe(0);
  });

  it('returns 0 for "0" (unlimited)', () => {
    expect(parseTimeLimit('0')).toBe(0);
  });

  it('returns 0 for empty string', () => {
    expect(parseTimeLimit('')).toBe(0);
  });

  it('parses valid time strings', () => {
    expect(parseTimeLimit('10m')).toBe(10);
    expect(parseTimeLimit('2h')).toBe(120);
    expect(parseTimeLimit('1d')).toBe(1440);
    expect(parseTimeLimit('1w')).toBe(10080);
    expect(parseTimeLimit('1M')).toBe(43200);
    expect(parseTimeLimit('1y')).toBe(525600);
  });

  it('returns 0 for invalid format', () => {
    expect(parseTimeLimit('invalid')).toBe(0);
    expect(parseTimeLimit('abc')).toBe(0);
  });
});

describe('formatAlertTimestamp', () => {
  it('formats timestamp in local time', () => {
    const result = formatAlertTimestamp('2024-01-15T14:30:00Z', false);
    expect(result).toContain('2024');
    expect(typeof result).toBe('string');
    expect(result).not.toBe('');
  });

  it('formats timestamp in UTC', () => {
    const result = formatAlertTimestamp('2024-01-15T14:30:00Z', true);
    expect(result).toContain('01/15/2024');
    expect(result).toContain('02:30:00 PM');
  });

  it('returns original string for invalid date', () => {
    expect(formatAlertTimestamp('not-a-date', false)).toBe('not-a-date');
  });

  it('handles numeric timestamp (ms)', () => {
    const timestamp = new Date('2024-06-15T10:00:00Z').getTime();
    const result = formatAlertTimestamp(timestamp, true);
    expect(result).toContain('06/15/2024');
  });

  it('returns string representation on error', () => {
    expect(formatAlertTimestamp('', false)).toBe('');
  });
});

describe('getCurrentTimeWindowLabel', () => {
  it('returns predefined label for known values', () => {
    expect(getCurrentTimeWindowLabel(10)).toBe('10m');
    expect(getCurrentTimeWindowLabel(60)).toBe('1h');
    expect(getCurrentTimeWindowLabel(120)).toBe('2h');
  });

  it('formats custom values', () => {
    expect(getCurrentTimeWindowLabel(45)).toBe('45m');
    expect(getCurrentTimeWindowLabel(1440)).toBe('1d');
    expect(getCurrentTimeWindowLabel(90)).toBe('1h 30m');
  });
});

describe('TIME_WINDOW_OPTIONS', () => {
  it('contains expected predefined options', () => {
    const values = TIME_WINDOW_OPTIONS.map(o => o.value);
    expect(values).toContain(10);
    expect(values).toContain(20);
    expect(values).toContain(30);
    expect(values).toContain(60);
    expect(values).toContain(120);
    expect(values).toContain(-1); // Custom
  });

  it('has Custom as last option with value -1', () => {
    const last = TIME_WINDOW_OPTIONS[TIME_WINDOW_OPTIONS.length - 1];
    expect(last.label).toBe('Custom');
    expect(last.value).toBe(-1);
  });
});
