// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import fs from 'fs';
import path from 'path';

jest.mock('next-runtime-env/build/configure', () => ({
  configureRuntimeEnv: () => ({}),
}));

const appRoot = path.resolve(__dirname, '..');

const HOT_RELOAD_PACKAGES: Record<string, string> = {
  common: path.join('common', 'lib-src'),
  '@nv-metropolis-bp-vss-ui/alerts': path.join('nv-metropolis-bp-vss-ui', 'alerts', 'lib-src'),
  '@nv-metropolis-bp-vss-ui/search': path.join('nv-metropolis-bp-vss-ui', 'search', 'lib-src'),
  '@nv-metropolis-bp-vss-ui/dashboard': path.join('nv-metropolis-bp-vss-ui', 'dashboard', 'lib-src'),
  '@nv-metropolis-bp-vss-ui/map': path.join('nv-metropolis-bp-vss-ui', 'map', 'lib-src'),
  '@nv-metropolis-bp-vss-ui/video-management': path.join(
    'nv-metropolis-bp-vss-ui',
    'video-management',
    'lib-src'
  ),
  '@nv-metropolis-bp-vss-ui/all': path.join('nv-metropolis-bp-vss-ui', 'all', 'lib-src'),
};

function loadNextConfig() {
  // next.config.js is CommonJS and loads next-runtime-env at require time.
  return require('../next.config.js') as {
    transpilePackages?: string[];
    webpack: (
      config: { experiments?: unknown; resolve: { alias: Record<string, string> } },
      options: { isServer: boolean; dev: boolean }
    ) => { resolve: { alias: Record<string, string> } };
  };
}

describe('hot reload smoke', () => {
  it('runs next dev with webpack so the lib-src aliases are applied', () => {
    const pkg = JSON.parse(fs.readFileSync(path.join(appRoot, 'package.json'), 'utf8')) as {
      scripts: { dev: string };
    };
    expect(pkg.scripts.dev).toMatch(/next\s+dev\b/);
    expect(pkg.scripts.dev).toMatch(/--webpack\b/);
  });

  it('transpiles workspace packages from source', () => {
    const nextConfig = loadNextConfig();
    expect(nextConfig.transpilePackages).toEqual(
      expect.arrayContaining(Object.keys(HOT_RELOAD_PACKAGES))
    );
  });

  it('aliases workspace packages to existing lib-src trees in development only', () => {
    const nextConfig = loadNextConfig();
    const packagesPath = path.resolve(appRoot, '../../packages');

    const devConfig = nextConfig.webpack(
      { resolve: { alias: { preexisting: '/keep' } } },
      { isServer: false, dev: true }
    );

    expect(devConfig.resolve.alias.preexisting).toBe('/keep');

    for (const [pkgName, relativeSrc] of Object.entries(HOT_RELOAD_PACKAGES)) {
      const aliasPath = devConfig.resolve.alias[pkgName];
      const expected = path.join(packagesPath, relativeSrc);
      expect(aliasPath).toBe(expected);
      expect(fs.existsSync(aliasPath)).toBe(true);
      expect(fs.existsSync(path.join(aliasPath, 'index.ts'))).toBe(true);
    }

    const prodConfig = nextConfig.webpack(
      { resolve: { alias: { preexisting: '/keep' } } },
      { isServer: false, dev: false }
    );
    expect(prodConfig.resolve.alias.preexisting).toBe('/keep');
    for (const pkgName of Object.keys(HOT_RELOAD_PACKAGES)) {
      expect(prodConfig.resolve.alias[pkgName]).toBeUndefined();
    }
  });
});
