/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { compression } from 'vite-plugin-compression2'
import { visualizer } from 'rollup-plugin-visualizer'

const isAnalyze = process.env.ANALYZE === 'true';
const isCompress = process.env.COMPRESS === 'true';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [
    react(),
    ...(isCompress ? [
      compression({
        algorithm: 'gzip',
        exclude: [/\.(br)$/, /\.(gz)$/],
      }),
      compression({
        algorithm: 'brotliCompress',
        exclude: [/\.(br)$/, /\.(gz)$/],
      })
    ] : []),
    ...(isAnalyze
      ? [visualizer({
          filename: 'dist/stats.html',
          open: true,
          gzipSize: true,
          brotliSize: true,
        })]
      : []),
  ],
  base: './',
  server: {
    host: '0.0.0.0',
    // Optional dev proxy: when developing the UI against a remote VIOS deployment, set the
    // VITE_BACKEND env var (e.g. `VITE_BACKEND=http://<host>:30888 npm run dev`) and point the
    // config endpoints at the dev origin (empty string). API + WebSocket calls are then proxied
    // same-origin to the backend, avoiding CORS. The ingress serves UI and API under /vst, so the
    // path is prefixed accordingly. Defaults to localhost when VITE_BACKEND is unset.
    proxy: (() => {
      const target = process.env.VITE_BACKEND || 'http://localhost:30888';
      const opts = { target, changeOrigin: true, secure: false, ws: true, rewrite: (p: string) => `/vst${p}` };
      return {
        '/api': opts,
        '/sensor': opts,
        '/storage': opts,
        '/record': opts,
        '/live': opts,
        '/streambridge': opts,
        '/replay': opts,
      };
    })(),
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        chunkFileNames: 'assets/js/[name]-[hash].js',
        entryFileNames: 'assets/js/[name]-[hash].js',
        assetFileNames: 'assets/[ext]/[name]-[hash].[ext]',
      },
    },
    chunkSizeWarningLimit: 1000, // Size in KB
    minify: 'terser',
    terserOptions: {
      compress: {
        // Disable console and debugger logs in production build
        drop_console: false,
        drop_debugger: false,
      },
    },
  },
})
