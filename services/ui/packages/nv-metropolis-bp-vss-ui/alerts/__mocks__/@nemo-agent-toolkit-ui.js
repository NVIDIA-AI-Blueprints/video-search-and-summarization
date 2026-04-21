// SPDX-License-Identifier: MIT
/**
 * Mock for @nemo-agent-toolkit/ui package
 * Used in Jest tests to avoid dependency on the full package
 */

const React = require('react');

const VideoModal = ({ isOpen, onClose, videoUrl, title }) => {
  if (!isOpen) return null;
  return React.createElement('div', { 'data-testid': 'video-modal' }, 
    `Video Modal: ${title || videoUrl || 'Video'}`
  );
};

module.exports = {
  VideoModal,
};

