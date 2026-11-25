// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React, { useEffect, useState } from 'react';
import Head from 'next/head';
import type { GetServerSideProps } from 'next';
import D1AgentPanel from '../components/D1AgentPanel';
import { appPath } from '../utils/basePath';

type Props = { embedded: boolean; initialTheme: 'dark' | 'light' };
export default function Monitor({ embedded, initialTheme }: Props) {
  const [theme, setTheme] = useState(initialTheme);
  useEffect(() => { document.documentElement.classList.toggle('dark', theme === 'dark'); }, [theme]);
  return <>
    <Head><title>Decision-model alerts | VSS Blueprint</title><meta name="viewport" content="width=device-width, initial-scale=1" /></Head>
    {!embedded && <header className="brand-header">
      <img src={appPath(theme === 'dark' ? '/NV-logo-white.svg' : '/NV-logo-black.svg')} alt="NVIDIA" />
      <span className="brand-title">VSS BLUEPRINT</span>
      <span className="brand-subtitle">D1 decision model + NVIDIA Cosmos</span>
      <button className="theme-button" type="button" aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`} onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? 'Light' : 'Dark'}</button>
    </header>}
    <main className={embedded ? 'embedded' : undefined}><D1AgentPanel isActive /></main>
  </>;
}
export const getServerSideProps: GetServerSideProps<Props> = async ({ query }) => ({
  props: { embedded: query.embed === '1', initialTheme: query.theme === 'light' ? 'light' : 'dark' },
});
