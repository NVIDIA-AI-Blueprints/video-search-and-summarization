// SPDX-License-Identifier: MIT

/** Ceiling on an import file, as a cheap guard against a decode bomb. 5MB. */
export const MAX_FILE_SIZE_BYTES = 5_242_880;

/** Cookie holding the chat session identifier. */
export const SESSION_COOKIE_NAME = 'vss-chat-session';
