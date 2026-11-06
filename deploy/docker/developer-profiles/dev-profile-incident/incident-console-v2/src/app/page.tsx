// SPDX-License-Identifier: Apache-2.0

import { AnalysisWorkspace } from '@/sections/analysis/components/AnalysisWorkspace';
import { AppHeader } from '@/sections/shell/components/AppHeader';

export default function HomePage() {
  return (
    <main className="relative min-h-screen overflow-hidden bg-canvas text-ink">
      <div className="ambient ambient-one" />
      <div className="ambient ambient-two" />

      <AppHeader active="analyze" />

      <section className="relative z-10 mx-auto grid max-w-[1440px] gap-12 px-6 pb-20 pt-6 lg:grid-cols-[0.88fr_1.12fr] lg:px-12 lg:pt-10">
        <div className="max-w-xl self-center">
          <p className="mb-5 font-mono text-xs font-semibold uppercase tracking-[0.24em] text-moss">From footage to facts</p>
          <h1 className="text-balance text-5xl font-semibold leading-[0.98] tracking-[-0.055em] sm:text-6xl xl:text-7xl">
            Understand the incident.
          </h1>
          <p className="mt-7 max-w-lg text-lg leading-8 text-ink/65">
            Drop in a video. Incident Studio will identify the critical sequence, organize the evidence, and prepare a report people can act
            on.
          </p>
        </div>

        <AnalysisWorkspace />
      </section>
    </main>
  );
}
