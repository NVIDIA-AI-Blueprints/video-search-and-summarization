// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { TextDecoder, TextEncoder } from 'node:util';

import '@testing-library/jest-dom';

// jsdom ships neither, and the SSE reader decodes every chunk with them.
if (!('TextEncoder' in globalThis)) {
  Object.assign(globalThis, { TextEncoder, TextDecoder });
}

// jsdom implements neither, and the panel calls both on every message.
window.HTMLElement.prototype.scrollIntoView = jest.fn();
if (!('ResizeObserver' in window)) {
  (window as any).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
