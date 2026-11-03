// SPDX-License-Identifier: Apache-2.0

import { Badge } from '@/components/ui/badge';

/** "Added by reviewer" and "Incomplete track" on a card; nothing for an AI item with a whole track. */
export function EvidenceBadges({ badges }: { badges: string[] }) {
  if (!badges.length) return null;
  return (
    <p className="flex flex-wrap gap-1" data-testid="evidence-badges">
      {badges.map((badge) => (
        <Badge className="px-1.5 py-0 text-[10px] font-semibold" key={badge} variant="secondary">
          {badge}
        </Badge>
      ))}
    </p>
  );
}
