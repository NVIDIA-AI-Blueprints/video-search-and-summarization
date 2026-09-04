// SPDX-License-Identifier: MIT
const React = require('react');

const VideoModal = ({ isOpen, title, videoUrl, onClose }) => {
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

// The real chunked-upload helper, so video-management tests exercise the
// actual chunking logic rather than a stub. ts-jest transpiles the .ts here.
const chunkedUploadModule = require('../../common/lib-src/utils/chunkedUpload');

module.exports = {
  VideoModal,
  VideoModalTooltip: () => null,
  UploadFilesDialog,
  UploadProgressPopup: () => null,
  UploadSuccessPopup: () => null,
  useVideoModal,
  copyToClipboard: jest.fn(),
  formatTimestamp: (value) => String(value),
  chunkedUpload: chunkedUploadModule.chunkedUpload,
  CHUNK_SIZE_BYTES: chunkedUploadModule.CHUNK_SIZE_BYTES,
  MAX_CHUNK_RETRIES: chunkedUploadModule.MAX_CHUNK_RETRIES,
};
