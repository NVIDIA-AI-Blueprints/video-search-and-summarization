<!-- SPDX-License-Identifier: MIT -->
# @nv-metropolis-bp-vss-ui/all

Aggregator package that re-exports all Metropolis VSS UI components. This package demonstrates SSR-enabled boilerplate architecture.

## Purpose

This package provides a single dependency entry point for all Metropolis VSS UI components. Instead of importing individual packages, you can import everything from this package.

## Features

- ✅ Server-Side Rendering (SSR) support
- ✅ Aggregates multiple component packages
- ✅ Separate client and server entry points
- ✅ TypeScript support
- ✅ SWC for fast compilation

## Usage

### In package.json

```json
{
  "dependencies": {
    "@nv-metropolis-bp-vss-ui/all": "*"
  }
}
```

### In your code

```typescript
// Client-side components
import { AlertsComponent, DashboardComponent, MapComponent } from '@nv-metropolis-bp-vss-ui/all';

// Server-side utilities (SSR)
import { serverFunction } from '@nv-metropolis-bp-vss-ui/all/server';

// Use the components
<AlertsComponent theme="dark" />
<DashboardComponent theme="light" />
```

## Included Components

This package re-exports:
- **AlertsComponent** from `@nv-metropolis-bp-vss-ui/alerts`
- **DashboardComponent** from `@nv-metropolis-bp-vss-ui/dashboard`
- **MapComponent** from `@nv-metropolis-bp-vss-ui/map`
- **SearchComponent** from `@nv-metropolis-bp-vss-ui/search`
- **VideoManagementComponent** from `@nv-metropolis-bp-vss-ui/video-management`

## Build

```bash
npm run build
```

This compiles the TypeScript source from `lib-src/` to `lib/` using SWC and generates type definitions.

## Scripts

- `npm run build` - Build the package
- `npm run clean` - Remove generated files and dependencies
- `npm run typecheck` - Type-check without emitting files

## Note

This is a convenience package and sample SSR-enabled boilerplate. All actual component implementations are in their respective packages:
- `@nv-metropolis-bp-vss-ui/alerts`
- `@nv-metropolis-bp-vss-ui/dashboard`
- `@nv-metropolis-bp-vss-ui/map`
- `@nv-metropolis-bp-vss-ui/search`
- `@nv-metropolis-bp-vss-ui/video-management`

This package simply re-exports them for easier consumption.
