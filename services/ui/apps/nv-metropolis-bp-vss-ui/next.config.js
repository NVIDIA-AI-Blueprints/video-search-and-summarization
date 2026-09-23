// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
const { configureRuntimeEnv } = require('next-runtime-env/build/configure');
const { i18n } = require('./next-i18next.config');

const nextConfig = {
  env: {
    ...configureRuntimeEnv(),
  },
  i18n,
  output: 'standalone',
  // Next 16 writes AGENTS.md and CLAUDE.md here on every dev run; this repo
  // keeps its agent guidance at the root instead.
  agentRules: false,
  // Transpile packages from source for hot reload during development
  transpilePackages: [
    'common',
    '@nv-metropolis-bp-vss-ui/all',
    '@nv-metropolis-bp-vss-ui/alerts',
    '@nv-metropolis-bp-vss-ui/chat',
    '@nv-metropolis-bp-vss-ui/search',
    '@nv-metropolis-bp-vss-ui/dashboard',
    '@nv-metropolis-bp-vss-ui/map',
    '@nv-metropolis-bp-vss-ui/video-management',
  ],
  typescript: {
    // !! WARN !!
    // Dangerously allow production builds to successfully complete even if
    // your project has type errors.
    // !! WARN !!
    ignoreBuildErrors: true,
  },
  experimental: {
    serverActions: {
      bodySizeLimit: '5mb',
    },
  },
  webpack(config, { isServer, dev }) {
    config.experiments = {
      asyncWebAssembly: true,
      layers: true,
    };

    // In development, resolve packages to their source code for hot reload
    if (dev) {
      const path = require('path');
      const packagesPath = path.resolve(__dirname, '../../packages');

      config.resolve.alias = {
        ...config.resolve.alias,
        'common': path.join(packagesPath, 'common/lib-src'),
        '@nv-metropolis-bp-vss-ui/alerts': path.join(packagesPath, 'nv-metropolis-bp-vss-ui/alerts/lib-src'),
        // Exact match only: the '/styles' subpath has no lib-src equivalent and
        // must keep resolving to lib/chat.css through the package exports.
        '@nv-metropolis-bp-vss-ui/chat$': path.join(packagesPath, 'nv-metropolis-bp-vss-ui/chat/lib-src'),
        '@nv-metropolis-bp-vss-ui/search': path.join(packagesPath, 'nv-metropolis-bp-vss-ui/search/lib-src'),
        '@nv-metropolis-bp-vss-ui/dashboard': path.join(packagesPath, 'nv-metropolis-bp-vss-ui/dashboard/lib-src'),
        '@nv-metropolis-bp-vss-ui/map': path.join(packagesPath, 'nv-metropolis-bp-vss-ui/map/lib-src'),
        '@nv-metropolis-bp-vss-ui/video-management': path.join(packagesPath, 'nv-metropolis-bp-vss-ui/video-management/lib-src'),
        '@nv-metropolis-bp-vss-ui/all': path.join(packagesPath, 'nv-metropolis-bp-vss-ui/all/lib-src'),
      };
    }

    return config;
  },
  async redirects() {
    return [];
  },
};

module.exports = nextConfig;
