// SPDX-License-Identifier: MIT
const React = require('react');

const VideoModal = ({ isOpen, title, videoUrl, onClose }) => {
  if (!isOpen) return null;
  return React.createElement('div', { 'data-testid': 'video-modal' },
    `Video Modal: ${title || videoUrl || 'Video'}`
  );
};

const UploadFilesDialog = () => null;
const UploadProgressPopup = () => React.createElement('div', { 'data-testid': 'upload-progress-popup' });
const UploadSuccessPopup = () => React.createElement('div', { 'data-testid': 'upload-success-popup' });

const useVideoModal = () => ({
  videoModal: { isOpen: false, videoUrl: '', title: '' },
  openVideoModal: jest.fn(() => Promise.resolve()),
  closeVideoModal: jest.fn(),
  openVideoModalFromUrl: jest.fn(),
  openVideoModalFromAlert: jest.fn(),
  loadingAlertId: null,
});

// Pull the real chunked-upload helper from common's source so video-management
// tests can exercise the actual chunking logic under test. ts-jest transpiles
// the .ts on the fly here.
const chunkedUploadModule = require('../../common/lib-src/utils/chunkedUpload');

module.exports = {
  useChatVideoUploadCompleteSubscription: jest.fn(),
  VideoModal,
  UploadFilesDialog,
  UploadProgressPopup,
  UploadSuccessPopup,
  useVideoModal,
  copyToClipboard: jest.fn(),
  chunkedUpload: chunkedUploadModule.chunkedUpload,
  CHUNK_SIZE_BYTES: chunkedUploadModule.CHUNK_SIZE_BYTES,
  MAX_CHUNK_RETRIES: chunkedUploadModule.MAX_CHUNK_RETRIES,
};
