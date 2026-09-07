// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Standalone VSS chat UI.
 *
 * Renders both surfaces the toolkit UI currently provides — a full chat pane
 * and a docked sidebar — without the toolkit being involved at all.
 *
 * Both panels post to this app's own /api/chat route, which proxies to the
 * agent backend server-side. The adapter therefore stays on a host-private
 * port and its address never reaches the browser; see pages/api/chat.ts.
 */
import { useMemo, useState } from 'react';
import Head from 'next/head';

import { ChatPanel } from '@nv-metropolis-bp-vss-ui/chat';

export default function Home() {
  const main = useMemo(() => ({ url: '/api/chat?surface=main' }), []);
  const sidebar = useMemo(() => ({ url: '/api/chat?surface=sidebar' }), []);

  const [sidebarOpen, setSidebarOpen] = useState(true);
  // Last answer from either panel, so the layout can demonstrate the hand-off
  // that feature tabs (search, alerts) will use in the real app.
  const [lastAnswer, setLastAnswer] = useState<string | null>(null);

  return (
    <>
      <Head>
        <title>VSS Chat</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>

      <div className="vss-shell">
        <header className="vss-shell-bar">
          <strong>VSS Chat</strong>
          <span className="vss-shell-note">
            no NeMo Agent Toolkit — UI or core
          </span>
          <button type="button" onClick={() => setSidebarOpen((v) => !v)}>
            {sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
          </button>
        </header>

        <main className="vss-shell-body">
          <div className="vss-shell-main">
            <ChatPanel
              endpoint={main}
              title="🔮 Hermes Agent"
              theme="dark"
              placeholder="Ask about your video…"
              onAnswer={(answer) => setLastAnswer(answer)}
            />
          </div>

          {sidebarOpen && (
            <aside className="vss-shell-side">
              <ChatPanel
                endpoint={sidebar}
                title="🦙 NemoClaw Agent"
                theme="light"
                placeholder="Ask the sandboxed agent…"
                onAnswer={(answer) => setLastAnswer(answer)}
              />
            </aside>
          )}
        </main>

        {lastAnswer && (
          <footer className="vss-shell-foot">
            last answer forwarded to feature tabs: {lastAnswer.slice(0, 120)}
            {lastAnswer.length > 120 ? '…' : ''}
          </footer>
        )}
      </div>
    </>
  );
}
