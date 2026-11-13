// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
const { configureRuntimeEnv } = require('next-runtime-env/build/configure');
const { i18n } = require('./next-i18next.config');

/**
 * Local Next has no haproxy in front. DASH start, the MPD, and segments all
 * live under `/vst` on the stack origin, which is a different host than the
 * UI, and `/vst/dash/` GET responses are not CORS-enabled. Same-origin `/vst`
 * plus this rewrite is how DashStream reaches VST from a dev server.
 */
function vstDevRewrites() {
  const raw = (
    process.env.NEXT_PUBLIC_WAREHOUSE_MEDIA_STREAMING_URL ||
    process.env.NEXT_PUBLIC_VST_API_URL ||
    ''
  ).trim();
  if (!raw) return [];
  let origin;
  try {
    origin = new URL(raw).origin;
  } catch {
    return [];
  }
  if (!origin || origin === 'null') return [];
  return [
    {
      source: '/vst/:path*',
      destination: `${origin}/vst/:path*`,
    },
  ];
}

const nextConfig = {
  env: {
    ...configureRuntimeEnv(),
  },
  i18n,
  output: 'standalone',
  // The UI does not use next/image; disabling the optimizer keeps sharp/libvips
  // off the request path of the /_next/image route.
  images: {
    unoptimized: true,
  },
  // Next 16 writes AGENTS.md and CLAUDE.md here on every dev run; this repo
  // keeps its agent guidance at the root instead.
  agentRules: false,
  // Dev assets are loaded from this hostname, not localhost.
  allowedDevOrigins: ['akshaya-ubuntu-hq-vm'],
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
    'vst-streaming-lib',
  ],
  typescript: {
    // !! WARN !!
    // Dangerously allow production builds to successfully complete even if
    // your project has type errors.
    // !! WARN !!
    ignoreBuildErrors: true,
  },
  experimental: {
    esmExternals: 'loose',
    serverActions: {
      bodySizeLimit: '5mb',
    },
  },
  webpack(config, { isServer, dev }) {
    const path = require('path');
    const packagesPath = path.resolve(__dirname, '../../packages');
    const warehouseSrc = path.join(
      packagesPath,
      'nv-metropolis-bp-vss-ui/map/lib-src/common/warehouse',
    );
    const streamingLibDash = path.resolve(
      __dirname,
      '../../../vios/ui/streaming-lib/src/dash/DashStream.ts',
    );
    // Webpack prefers dashjs' "browser" export (UMD). That module is
    // `{ default: { MediaPlayer } }`, so `import dashjs from 'dashjs'` has no
    // MediaPlayer. The ESM build matches VIOS/Vite (`export { MediaPlayer, default }`).
    const dashjsEntry = path.resolve(
      __dirname,
      '../../node_modules/dashjs/dist/modern/esm/dash.all.min.js',
    );

    config.experiments = {
      asyncWebAssembly: true,
      layers: true,
    };

    config.resolve.fallback = {
      ...(config.resolve.fallback || {}),
      canvas: false,
      fs: false,
      net: false,
      tls: false,
    };

    // Konva's Node entry requires the native `canvas` package. Warehouse
    // analytics is client-only, so always resolve the browser build.
    // Warehouse DASH imports DashStream only, not StreamManager/WebRTC.
    try {
      config.resolve.alias = {
        ...(config.resolve.alias || {}),
        canvas: false,
        'common/warehouse': warehouseSrc,
        'konva/lib/index-node': require.resolve('konva/lib/index.js'),
        'konva/lib/index-node.js': require.resolve('konva/lib/index.js'),
        'vst-streaming-lib/dash$': streamingLibDash,
        dashjs: dashjsEntry,
      };
    } catch {
      config.resolve.alias = {
        ...(config.resolve.alias || {}),
        canvas: false,
        'common/warehouse': warehouseSrc,
        'vst-streaming-lib/dash$': streamingLibDash,
        dashjs: dashjsEntry,
      };
    }

    // In development, resolve packages to their source code for hot reload
    if (dev) {
      config.resolve.alias = {
        ...config.resolve.alias,
        'common/warehouse': warehouseSrc,
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
  async rewrites() {
    return vstDevRewrites();
  },
};

module.exports = nextConfig;
