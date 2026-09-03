// SPDX-License-Identifier: MIT
import React from 'react';

// Check if a ReactNode has displayable content WITHOUT executing components.
// IMPORTANT: never call function components directly (that breaks Hooks rules).
export const hasComponentContent = (element: React.ReactNode): boolean => {
  if (element === null || element === undefined || element === false) return false;
  if (typeof element === 'string') return element.trim().length > 0;
  if (typeof element === 'number') return true;
  if (Array.isArray(element)) return element.some(hasComponentContent);
  return React.isValidElement(element);
};

export const hasComponentContentArray = (elements: React.ReactNode[]): boolean[] => {
  return elements.map(hasComponentContent);
};
