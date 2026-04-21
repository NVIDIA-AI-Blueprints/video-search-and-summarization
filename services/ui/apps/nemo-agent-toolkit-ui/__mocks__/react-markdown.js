/**
 * Mock for react-markdown to avoid ESM transformation issues in Jest
 */

import React from 'react';

const ReactMarkdown = ({ children, ...props }) => {
  return React.createElement('div', { ...props, 'data-testid': 'react-markdown' }, children);
};

export default ReactMarkdown;