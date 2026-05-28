// SPDX-License-Identifier: MIT
/**
 * TimeFormatSwitch – UTC / Local sliding segmented control for alert timestamp display.
 */

import React from 'react';

export type TimeFormat = 'local' | 'utc';

interface TimeFormatSwitchProps {
  value: TimeFormat;
  onChange: (format: TimeFormat) => void;
  isDark: boolean;
}

function getButtonTextClass(isActive: boolean, isDark: boolean): string {
  if (isActive) return isDark ? 'text-black' : 'text-white';
  return isDark ? 'text-gray-400 hover:text-gray-300' : 'text-gray-600 hover:text-gray-800';
}

export const TimeFormatSwitch: React.FC<TimeFormatSwitchProps> = ({
  value,
  onChange,
  isDark
}) => (
  <fieldset
    aria-label="Time zone display"
    className={`relative flex rounded-md p-0.5 min-w-[120px] border-0 m-0 ${isDark ? 'bg-neutral-900' : 'bg-gray-300'}`}
  >
    <div
      className={`absolute top-0.5 bottom-0.5 w-[calc(50%-4px)] rounded-[5px] transition-all duration-200 ease-out ${
        value === 'local' ? 'left-0.5' : 'left-[calc(50%+2px)]'
      } bg-[#76b900]`}
      aria-hidden
    />
    <button
      type="button"
      onClick={() => onChange('local')}
      className={`relative z-10 flex-1 text-sm font-medium px-4 py-1 rounded-[5px] transition-colors ${getButtonTextClass(value === 'local', isDark)}`}
      title="Show times in local timezone"
    >
      Local
    </button>
    <button
      type="button"
      onClick={() => onChange('utc')}
      className={`relative z-10 flex-1 text-sm font-medium px-4 py-1 rounded-[5px] transition-colors ${getButtonTextClass(value === 'utc', isDark)}`}
      title="Show times in UTC"
    >
      UTC
    </button>
  </fieldset>
);
