const { configureRuntimeEnv } = require('next-runtime-env/build/configure');
const { i18n } = require('./next-i18next.config');

const nextConfig = {
  env: {
    ...configureRuntimeEnv(),
  },
  i18n,
  output: 'standalone',
  typescript: {
    // !! WARN !!
    // Dangerously allow production builds to successfully complete even if
    // your project has type errors.
    // !! WARN !!
    ignoreBuildErrors: true,
  },
  experimental: {
    // Serialize static generation; parallel workers can hit missing build-manifest during i18n prerender.
    cpus: 1,
    serverActions: {
      bodySizeLimit: '5mb',
    },
  },
  webpack(config, { isServer, dev }) {
    config.experiments = {
      asyncWebAssembly: true,
      layers: true,
    };

    return config;
  },
  async redirects() {
    return [];
  },
};

module.exports = nextConfig;
