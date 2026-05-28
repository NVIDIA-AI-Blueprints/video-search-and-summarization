// SPDX-License-Identifier: MIT
import React from 'react';
import { hasComponentContent, hasComponentContentArray } from '../../utils';

describe('hasComponentContent', () => {
  it('returns false for null', () => {
    expect(hasComponentContent(null)).toBe(false);
  });

  it('returns false for undefined', () => {
    expect(hasComponentContent(undefined)).toBe(false);
  });

  it('returns false for a plain string', () => {
    expect(hasComponentContent('hello')).toBe(false);
  });

  it('returns false for a number', () => {
    expect(hasComponentContent(42)).toBe(false);
  });

  it('returns false for a boolean', () => {
    expect(hasComponentContent(false)).toBe(false);
  });

  it('returns true for a function component that returns content', () => {
    const Comp = () => React.createElement('div', null, 'content');
    const element = React.createElement(Comp);
    expect(hasComponentContent(element)).toBe(true);
  });

  it('returns false for a function component that returns null', () => {
    const Comp = () => null;
    const element = React.createElement(Comp);
    expect(hasComponentContent(element)).toBe(false);
  });

  it('returns false for a host element (div, span) since type is a string, not a function', () => {
    const element = React.createElement('div', null, 'text');
    expect(hasComponentContent(element)).toBe(false);
  });
});

describe('hasComponentContentArray', () => {
  it('returns an array of booleans matching each element', () => {
    const HasContent = () => React.createElement('div');
    const NoContent = () => null;

    const results = hasComponentContentArray([
      React.createElement(HasContent),
      React.createElement(NoContent),
      null,
    ]);

    expect(results).toEqual([true, false, false]);
  });

  it('returns empty array for empty input', () => {
    expect(hasComponentContentArray([])).toEqual([]);
  });
});
