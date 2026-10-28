// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
declare module '*.module.css' {
  const classes: Record<string, string>;
  export default classes;
}
declare module '*.css';
declare module '*.png' {
  const src: string;
  export default src;
}
declare module '*.svg' {
  const src: string;
  export default src;
}
declare module '*.json' {
  const value: unknown;
  export default value;
}
declare module 'wellknown';
declare module 'react-marquee-text';
declare module 'webrtc-streamer/html/libs/adapter.min.js';
declare module 'ts-overlapping-marker-spiderfier';
