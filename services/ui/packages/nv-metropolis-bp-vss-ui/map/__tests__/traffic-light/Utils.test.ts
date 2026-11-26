// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { SpeedToTrafficColor } from '../../lib-src/traffic-light/utils/Utils';

describe('SpeedToTrafficColor', () => {
  it('returns gray for negative speed', () => {
    expect(SpeedToTrafficColor(-1)).toBe('#A0A0A0');
    expect(SpeedToTrafficColor(-100)).toBe('#A0A0A0');
  });

  it('returns red for speed 0-10', () => {
    expect(SpeedToTrafficColor(0)).toBe('#E60000');
    expect(SpeedToTrafficColor(5)).toBe('#E60000');
    expect(SpeedToTrafficColor(10)).toBe('#E60000');
  });

  it('returns light red for speed 11-20', () => {
    expect(SpeedToTrafficColor(11)).toBe('#F08080');
    expect(SpeedToTrafficColor(20)).toBe('#F08080');
  });

  it('returns orange for speed 21-30', () => {
    expect(SpeedToTrafficColor(21)).toBe('#FFA769');
    expect(SpeedToTrafficColor(30)).toBe('#FFA769');
  });

  it('returns light green for speed 31-45', () => {
    expect(SpeedToTrafficColor(31)).toBe('#98FB98');
    expect(SpeedToTrafficColor(45)).toBe('#98FB98');
  });

  it('returns green for speed above 45', () => {
    expect(SpeedToTrafficColor(46)).toBe('#3CB371');
    expect(SpeedToTrafficColor(100)).toBe('#3CB371');
  });

  it('handles exact boundary values', () => {
    expect(SpeedToTrafficColor(0)).toBe('#E60000');
    expect(SpeedToTrafficColor(10)).toBe('#E60000');
    expect(SpeedToTrafficColor(11)).toBe('#F08080');
    expect(SpeedToTrafficColor(20)).toBe('#F08080');
    expect(SpeedToTrafficColor(21)).toBe('#FFA769');
    expect(SpeedToTrafficColor(30)).toBe('#FFA769');
    expect(SpeedToTrafficColor(31)).toBe('#98FB98');
    expect(SpeedToTrafficColor(45)).toBe('#98FB98');
    expect(SpeedToTrafficColor(46)).toBe('#3CB371');
  });
});
