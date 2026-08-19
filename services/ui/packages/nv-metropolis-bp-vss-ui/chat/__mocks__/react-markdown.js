// SPDX-License-Identifier: MIT
// react-markdown ships ESM only, which jest cannot parse. Rendering markdown
// is not what these suites assert, so the content passes through verbatim.
const React = require('react');

const ReactMarkdown = ({ children, ...props }) =>
  React.createElement('div', { ...props, 'data-testid': 'react-markdown' }, children);

module.exports = ReactMarkdown;
module.exports.default = ReactMarkdown;
module.exports.__esModule = true;
