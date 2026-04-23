// SPDX-License-Identifier: MIT
import { formatTimestamp } from '../../lib-src/utils/formatters';

describe('formatTimestamp', () => {
  it('formats ISO timestamp to locale date and time', () => {
    const result = formatTimestamp('2024-01-15T14:30:00Z');
    expect(result).toMatch(/\d{1,2}\/\d{1,2}\/\d{4}/);
    expect(result).toMatch(/\d{1,2}:\d{2}:\d{2}\s*(AM|PM)/);
  });

  it('returns "Invalid Date" formatted string for invalid date', () => {
    const result = formatTimestamp('not-a-date');
    expect(result).toContain('Invalid Date');
  });

  it('returns "Invalid Date" for empty string', () => {
    const result = formatTimestamp('');
    expect(result).toContain('Invalid Date');
  });

  it('handles valid ISO format with timezone', () => {
    const result = formatTimestamp('2024-06-15T10:00:00.000Z');
    expect(typeof result).toBe('string');
    expect(result.length).toBeGreaterThan(0);
  });
});
