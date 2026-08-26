// SPDX-License-Identifier: MIT
import type { AppProps } from 'next/app';

import '@nv-metropolis-bp-vss-ui/chat/styles';
import '../styles/global.css';

export default function VssChatApp({ Component, pageProps }: AppProps) {
  return <Component {...pageProps} />;
}
