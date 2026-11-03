// SPDX-License-Identifier: Apache-2.0

'use client';

import Link from 'next/link';

import { CheckIcon, MenuIcon } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { NavigationMenu, NavigationMenuItem, NavigationMenuLink, NavigationMenuList } from '@/components/ui/navigation-menu';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { cn } from 'cn';
import { forgetLibraryState } from '@/lib/library/remembered-state';
import { HealthStrip } from '@/sections/shell/components/HealthStrip';

const NAV_ITEMS = [
  { href: '/', key: 'analyze', label: 'Analyze' },
  { href: '/library', key: 'library', label: 'Library' },
  { href: '/dashboard', key: 'dashboard', label: 'Dashboard' },
] as const;

export function AppHeader({
  active,
  subtitle = 'Video intelligence',
}: {
  active?: 'analyze' | 'library' | 'dashboard';
  subtitle?: string;
}) {
  return (
    <header className="print-hidden relative z-10 mx-auto flex w-full max-w-[1500px] items-center justify-between gap-3 px-4 py-5 sm:gap-4 sm:px-6 sm:py-7 lg:px-12">
      <Link
        className="flex min-w-0 items-center gap-3 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        href="/"
        aria-label="Incident Studio home"
      >
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-ink text-sm font-bold text-white">IS</span>
        <span className="min-w-0">
          <span className="block truncate text-sm font-semibold tracking-tight">Incident Studio</span>
          <span className="block truncate text-[11px] uppercase tracking-[0.2em] text-ink/50">{subtitle}</span>
        </span>
      </Link>
      <NavigationMenu aria-label="Primary navigation" className="hidden flex-none sm:flex" viewport={false}>
        <NavigationMenuList className="gap-2">
          {NAV_ITEMS.map((item) => (
            <NavigationMenuItem key={item.key}>
              <NavigationMenuLink
                asChild
                active={active === item.key}
                className={cn(
                  'px-3 py-1.5 text-xs font-medium sm:text-sm',
                  active === item.key
                    ? 'bg-primary font-bold text-primary-foreground hover:bg-primary/90 hover:text-primary-foreground focus:bg-primary focus:text-primary-foreground data-[active=true]:bg-primary data-[active=true]:hover:bg-primary/90 data-[active=true]:focus:bg-primary'
                    : 'text-ink/70 hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground',
                )}
              >
                <Link
                  aria-current={active === item.key ? 'page' : undefined}
                  href={item.href}
                  onClick={item.key === 'library' ? () => forgetLibraryState() : undefined}
                >
                  {item.label}
                </Link>
              </NavigationMenuLink>
            </NavigationMenuItem>
          ))}
        </NavigationMenuList>
      </NavigationMenu>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button aria-label="Open navigation menu" className="shrink-0 sm:hidden" size="icon" variant="outline">
            <MenuIcon aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {NAV_ITEMS.map((item) => (
            <DropdownMenuItem asChild key={item.key}>
              <Link
                aria-current={active === item.key ? 'page' : undefined}
                className={cn(
                  'flex items-center justify-between gap-3',
                  active === item.key && 'bg-primary font-bold text-primary-foreground focus:bg-primary/90 focus:text-primary-foreground',
                )}
                href={item.href}
                onClick={item.key === 'library' ? () => forgetLibraryState() : undefined}
              >
                {item.label}
                {active === item.key && <CheckIcon aria-hidden="true" className="size-4" />}
              </Link>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <div className="hidden lg:block">
        <HealthStrip />
      </div>
    </header>
  );
}
