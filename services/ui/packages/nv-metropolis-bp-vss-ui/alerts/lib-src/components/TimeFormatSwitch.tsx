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

export const TimeFormatSwitch: React.FC<TimeFormatSwitchProps> = ({
  value,
  onChange,
  isDark
}) => (
  <div className="flex items-center gap-2">
    <span className={`text-sm font-medium whitespace-nowrap ${isDark ? 'text-gray-300' : 'text-gray-700'}`}>
      Time:
    </span>
    <div
      role="group"
      aria-label="Time zone display"
      className={`relative flex rounded-md p-0.5 min-w-[140px] ${isDark ? 'bg-gray-900' : 'bg-gray-300'}`}
    >
      <div
        className={`absolute top-0.5 bottom-0.5 w-[calc(50%-4px)] rounded-[5px] transition-all duration-200 ease-out ${
          value === 'local' ? 'left-0.5' : 'left-[calc(50%+2px)]'
        } ${isDark ? 'bg-cyan-600' : 'bg-blue-600'}`}
        aria-hidden
      />
      <button
        type="button"
        onClick={() => onChange('local')}
        className={`relative z-10 flex-1 text-sm font-medium px-5 py-1.5 rounded-[5px] transition-colors ${
          value === 'local' ? 'text-white' : isDark ? 'text-gray-400 hover:text-gray-300' : 'text-gray-600 hover:text-gray-800'
        }`}
        title="Show times in local timezone"
      >
        Local
      </button>
      <button
        type="button"
        onClick={() => onChange('utc')}
        className={`relative z-10 flex-1 text-sm font-medium px-5 py-1.5 rounded-[5px] transition-colors ${
          value === 'utc' ? 'text-white' : isDark ? 'text-gray-400 hover:text-gray-300' : 'text-gray-600 hover:text-gray-800'
        }`}
        title="Show times in UTC"
      >
        UTC
      </button>
    </div>
  </div>
);
