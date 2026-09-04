// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
/**
 * Mock for @nv-metropolis-bp-vss-ui/chat.
 *
 * The feature packages only reach into chat for the upload-completion
 * subscription; the panel itself is tested in that package.
 */
module.exports = {
  useChatVideoUploadCompleteSubscription: jest.fn(),
};
