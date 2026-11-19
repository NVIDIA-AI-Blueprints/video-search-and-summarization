// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '';
if (basePath && !/^\/[a-zA-Z0-9_-]+(?:\/[a-zA-Z0-9_-]+)*$/.test(basePath)) {
  throw new Error('NEXT_PUBLIC_BASE_PATH must be an absolute path without a trailing slash');
}
module.exports = {
  output: 'standalone', basePath,
  poweredByHeader: false,
  experimental: { cpus: 2 },
  async headers() {
    return [{ source: '/:path*', headers: [
      { key: 'Content-Security-Policy', value: "frame-ancestors 'self'" },
      { key: 'X-Content-Type-Options', value: 'nosniff' },
      { key: 'Referrer-Policy', value: 'same-origin' },
    ] }];
  },
};
