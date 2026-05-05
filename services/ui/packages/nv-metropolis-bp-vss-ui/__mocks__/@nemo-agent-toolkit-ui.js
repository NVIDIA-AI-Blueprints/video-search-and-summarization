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

// Pull the real chunked-upload helper from common's source so video-management
// tests can exercise the actual chunking logic under test (@nemo-agent-toolkit/ui
// re-exports it at runtime). ts-jest transpiles the .ts on the fly here.
const chunkedUploadModule = require('../../common/lib-src/utils/chunkedUpload');

module.exports = {
  VideoModal,
  UploadFilesDialog,
  useVideoModal,
  uploadFile: jest.fn(),
  copyToClipboard: jest.fn(),
  uploadFileChunked: chunkedUploadModule.uploadFileChunked,
  CHUNK_SIZE_BYTES: chunkedUploadModule.CHUNK_SIZE_BYTES,
  MAX_CHUNK_RETRIES: chunkedUploadModule.MAX_CHUNK_RETRIES,
};
