// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import fs from 'fs';
import path from 'path';

jest.mock('next-runtime-env/build/configure', () => ({
  configureRuntimeEnv: () => ({}),
}));

const appRoot = path.resolve(__dirname, '..');

/**
 * Every workspace package the app edits during development, with the webpack
 * alias key that points it at source. `chat` uses exact-match keys for its
 * TypeScript entry point and stylesheet.
 */
const HOT_RELOAD_PACKAGES: { name: string; aliasKey: string; src: string }[] = [
  { name: 'common', aliasKey: 'common', src: path.join('common', 'lib-src') },
  ...['alerts', 'search', 'dashboard', 'map', 'video-management', 'all'].map((pkg) => ({
    name: `@nv-metropolis-bp-vss-ui/${pkg}`,
    aliasKey: `@nv-metropolis-bp-vss-ui/${pkg}`,
    src: path.join('nv-metropolis-bp-vss-ui', pkg, 'lib-src'),
  })),
  {
    name: '@nv-metropolis-bp-vss-ui/chat',
    aliasKey: '@nv-metropolis-bp-vss-ui/chat$',
    src: path.join('nv-metropolis-bp-vss-ui', 'chat', 'lib-src'),
  },
];

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
      expect.arrayContaining(HOT_RELOAD_PACKAGES.map((pkg) => pkg.name))
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

    for (const { aliasKey, src } of HOT_RELOAD_PACKAGES) {
      const aliasPath = devConfig.resolve.alias[aliasKey];
      expect(aliasPath).toBe(path.join(packagesPath, src));
      expect(fs.existsSync(aliasPath)).toBe(true);
      expect(fs.existsSync(path.join(aliasPath, 'index.ts'))).toBe(true);
    }

    const prodConfig = nextConfig.webpack(
      { resolve: { alias: { preexisting: '/keep' } } },
      { isServer: false, dev: false }
    );
    expect(prodConfig.resolve.alias.preexisting).toBe('/keep');
    for (const { aliasKey } of HOT_RELOAD_PACKAGES) {
      expect(prodConfig.resolve.alias[aliasKey]).toBeUndefined();
    }
  });

  it('aliases the chat stylesheet to source without rewriting other subpaths', () => {
    const nextConfig = loadNextConfig();
    const packagesPath = path.resolve(appRoot, '../../packages');
    const devConfig = nextConfig.webpack(
      { resolve: { alias: {} } },
      { isServer: false, dev: true }
    );

    expect(devConfig.resolve.alias['@nv-metropolis-bp-vss-ui/chat']).toBeUndefined();
    expect(devConfig.resolve.alias['@nv-metropolis-bp-vss-ui/chat$']).toBeDefined();
    const stylesheet = devConfig.resolve.alias['@nv-metropolis-bp-vss-ui/chat/styles$'];
    expect(stylesheet).toBe(
      path.join(packagesPath, 'nv-metropolis-bp-vss-ui/chat/lib-src/chat.css')
    );
    expect(fs.existsSync(stylesheet)).toBe(true);
  });
});
