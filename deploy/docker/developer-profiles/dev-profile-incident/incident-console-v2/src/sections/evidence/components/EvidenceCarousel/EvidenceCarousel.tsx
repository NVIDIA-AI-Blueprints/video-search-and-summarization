// SPDX-License-Identifier: Apache-2.0
'use client';

import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react';
import { useRef, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';

export function EvidenceCarousel({ children, label }: { children: ReactNode; label: string }) {
  const rail = useRef<HTMLDivElement>(null);
  const move = (direction: -1 | 1) => {
    const node = rail.current;
    if (!node) return;
    node.scrollBy({ behavior: 'smooth', left: direction * Math.max(280, node.clientWidth * 0.8) });
  };

  return (
    <section aria-label={label} className="relative">
      <div className="mb-2 flex justify-end gap-2">
        <Button aria-label="Previous evidence cards" onClick={() => move(-1)} size="icon-sm" type="button" variant="outline">
          <ChevronLeftIcon aria-hidden="true" />
        </Button>
        <Button aria-label="Next evidence cards" onClick={() => move(1)} size="icon-sm" type="button" variant="outline">
          <ChevronRightIcon aria-hidden="true" />
        </Button>
      </div>
      <div className="flex snap-x snap-mandatory gap-3 overflow-x-auto pb-3" ref={rail}>
        {children}
      </div>
    </section>
  );
}
