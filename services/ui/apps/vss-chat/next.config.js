// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  // The chat package ships untranspiled ESM-ish output from swc.
  transpilePackages: ['@nv-metropolis-bp-vss-ui/chat'],
};

module.exports = nextConfig;
