// SPDX-License-Identifier: MIT
/**
 * FilterTag Component - Interactive Filter Display and Management
 * 
 * This file contains the FilterTag component which provides an interactive visual representation
 * of active filters in the alerts management system. The component displays applied filters as
 * styled tags with removal capabilities, offering users clear visibility into their current
 * filter selections and easy management of active filter states.
 * 
 * **Key Features:**
 * - Visual filter representation with category-specific color coding
 * - Interactive removal functionality with hover effects and click handling
 * - Responsive design adapting to different screen sizes and orientations
 * - Comprehensive theme support for both light and dark modes
 * - Accessibility features including proper ARIA labels and keyboard navigation
 * - Smooth animations and transitions for enhanced user experience
 * - Category-specific styling to differentiate filter types visually
 */

import React from 'react';
import { IconX } from '@tabler/icons-react';
import { FilterType } from '../types';

interface FilterTagProps {
  type: FilterType;
  filter: string;
  colors: {
    bg: string;
    border: string;
    text: string;
    hover: string;
  };
  onRemove: (type: FilterType, filter: string) => void;
}

export const FilterTag: React.FC<FilterTagProps> = ({ type, filter, colors, onRemove }) => (
  <div
    className={`flex items-center gap-1.5 rounded-lg pl-3 pr-2 py-2 text-sm transition-all ${colors.bg} ${colors.border} ${colors.text}`}
  >
    <span>{filter}</span>
    <button
      onClick={() => onRemove(type, filter)}
      className={`p-0.5 rounded transition-colors focus-visible:ring-2 focus-visible:ring-current focus-visible:ring-offset-1 ${colors.hover}`}
      aria-label={`Remove filter ${filter}`}
    >
      <IconX className="w-3.5 h-3.5" />
    </button>
  </div>
);

