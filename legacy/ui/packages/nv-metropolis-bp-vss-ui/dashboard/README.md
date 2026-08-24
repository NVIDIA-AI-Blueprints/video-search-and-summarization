<!-- SPDX-License-Identifier: MIT -->
# @nv-metropolis-bp-vss-ui/dashboard

A React component for embedding Kibana dashboards and other analytics platforms.

## Features

- ✅ Server-Side Rendering (SSR) support
- ✅ Secure iframe embedding with configurable sandbox attributes
- ✅ Loading and error states with retry functionality
- ✅ URL validation and sanitization
- ✅ TypeScript support
- ✅ Theme support (light/dark)

## Build

```bash
npm run build
```

This compiles the TypeScript source from `lib-src/` to `lib/` using SWC and generates type definitions.

## Usage

```typescript
// Client-side components
import { DashboardComponent } from '@nv-metropolis-bp-vss-ui/dashboard';

// Server-side utilities (SSR)
import { fetchDashboardData } from '@nv-metropolis-bp-vss-ui/dashboard/server';

// Example usage
const dashboardData = await fetchDashboardData();

<DashboardComponent 
  theme="light" 
  dashboardData={dashboardData}
/>
```

## Configuration

Set the Kibana dashboard URL via environment variable:

```bash
NEXT_PUBLIC_KIBANA_DASHBOARD_URL=http://your-kibana-instance:5601/app/dashboards
```

## Scripts

- `npm run build` - Build the package
- `npm run clean` - Remove generated files and dependencies
- `npm run typecheck` - Type-check without emitting files
