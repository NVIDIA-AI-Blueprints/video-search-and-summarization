/** @type {import('next').NextConfig} */
// SPDX-License-Identifier: MIT
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  // The chat package ships untranspiled ESM-ish output from swc.
  transpilePackages: ['@nv-metropolis-bp-vss-ui/chat'],
};

module.exports = nextConfig;
