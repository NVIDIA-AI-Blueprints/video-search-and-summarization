// SPDX-License-Identifier: MIT

/**
 * Parent-app supplied metadata rendered in the caller-info section of assistant
 * responses. Owned here rather than by the chat implementation so host apps can
 * produce it without depending on a specific chat UI.
 */
export type CallerInfo = string;
