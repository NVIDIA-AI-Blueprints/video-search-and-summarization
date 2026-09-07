// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import type { AppProps } from 'next/app';

import '@nv-metropolis-bp-vss-ui/chat/styles';
import '../styles/global.css';

export default function VssChatApp({ Component, pageProps }: AppProps) {
  return <Component {...pageProps} />;
}
