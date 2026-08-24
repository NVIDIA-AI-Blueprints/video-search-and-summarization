// SPDX-License-Identifier: MIT
import React from 'react';

// Check if React element renders content (not null)
export const hasComponentContent = (element: React.ReactNode): boolean => {
  if (!element || !React.isValidElement(element)) return false;
  const { type, props } = element;
  if (typeof type === 'function') {
    return !!(type as Function)(props);
  }
  return false;
};

export const hasComponentContentArray = (elements: React.ReactNode[]): boolean[] => {
  return elements.map(hasComponentContent);
};
