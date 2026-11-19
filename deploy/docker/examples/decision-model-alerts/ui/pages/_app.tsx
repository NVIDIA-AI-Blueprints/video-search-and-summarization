// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import type { AppProps } from 'next/app';
import '../styles/globals.css';
export default function App({ Component, pageProps }: AppProps) { return <Component {...pageProps} />; }
