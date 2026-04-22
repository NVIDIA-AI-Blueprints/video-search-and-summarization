<!-- SPDX-License-Identifier: MIT -->
# @nv-metropolis-bp-vss-ui/search

A sample component package demonstrating SSR-enabled boilerplate architecture.

## Features

- ✅ Server-Side Rendering (SSR) support
- ✅ Separate client and server entry points
- ✅ TypeScript support
- ✅ SWC for fast compilation

## Build

```bash
npm run build
```

This compiles the TypeScript source from `lib-src/` to `lib/` using SWC and generates type definitions.

## Usage

```typescript
// Client-side components
import { SearchComponent } from '@nv-metropolis-bp-vss-ui/search';

// Server-side utilities (SSR)
import { serverFunction } from '@nv-metropolis-bp-vss-ui/search/server';
```

## Scripts

- `npm run build` - Build the package
- `npm run clean` - Remove generated files and dependencies
- `npm run typecheck` - Type-check without emitting files

