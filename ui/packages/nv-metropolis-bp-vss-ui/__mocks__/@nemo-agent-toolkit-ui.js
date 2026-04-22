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

const UploadFilesDialog = () => null;

const useVideoModal = () => ({
  videoModal: { isOpen: false, videoUrl: '', title: '' },
  openVideoModal: jest.fn(() => Promise.resolve()),
  closeVideoModal: jest.fn(),
  openVideoModalFromUrl: jest.fn(),
  openVideoModalFromAlert: jest.fn(),
  loadingAlertId: null,
});

module.exports = {
  VideoModal,
  UploadFilesDialog,
  useVideoModal,
  uploadFile: jest.fn(),
  copyToClipboard: jest.fn(),
};
