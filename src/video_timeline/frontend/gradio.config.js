// SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: LicenseRef-NvidiaProprietary
//
// NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
// property and proprietary rights in and to this material, related
// documentation and any modifications thereto. Any use, reproduction,
// disclosure or distribution of this material and related documentation
// without an express license agreement from NVIDIA CORPORATION or
// its affiliates is strictly prohibited.

import commonjs from "vite-plugin-commonjs";

export default {
  plugins: [
    commonjs({
      filter(id) { return id.includes("node_modules/deepmerge"); }
    })
  ],
  svelte: {
    preprocess: [],
  },
  build: {
    target: "modules",
  },
  optimizeDeps: {
    include: ['svelte/animate', 'svelte/easing', 'svelte/internal', 'svelte/internal/disclose-version', 'svelte/motion', 'svelte/store', 'svelte/transition', 'svelte'],
    exclude: []
  },
  resolve: {
    dedupe: ['svelte']
  }
};