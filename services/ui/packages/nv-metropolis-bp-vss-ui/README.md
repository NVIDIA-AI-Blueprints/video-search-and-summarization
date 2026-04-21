<!-- SPDX-License-Identifier: MIT -->
# Metropolis VSS UI Components

This directory contains reusable UI components for the Metropolis VSS application.

## Packages

- **@nv-metropolis-bp-vss-ui/alerts** - Alerts dashboard component
- **@nv-metropolis-bp-vss-ui/dashboard** - Dashboard component
- **@nv-metropolis-bp-vss-ui/map** - Map component
- **@nv-metropolis-bp-vss-ui/search** - Search component
- **@nv-metropolis-bp-vss-ui/video-management** - Video Management component

## Setup

### 1. Install Dependencies

From the root of the monorepo:

```bash
npm install
```

This will install all dependencies for the workspace packages.

### 2. Build the Packages

Build all packages in the monorepo:

```bash
npx turbo build --filter=./packages/nv-metropolis-bp-vss-ui/*
```

Or build individual packages:

```bash
# Build alerts package
cd packages/nv-metropolis-bp-vss-ui/alerts
npm run build

# Build dashboard package
cd packages/nv-metropolis-bp-vss-ui/dashboard
npm run build
```

### 3. Use in Applications

The packages are automatically linked through npm workspaces. Import them in your applications:

```typescript
import { AlertsComponent } from '@nv-metropolis-bp-vss-ui/alerts';
import { DashboardComponent } from '@nv-metropolis-bp-vss-ui/dashboard';
import { MapComponent } from '@nv-metropolis-bp-vss-ui/map';
```

## Development

### Package Structure

Each package follows this structure:

```
package-name/
├── lib-src/          # Source TypeScript files
│   ├── index.ts      # Main export file
│   └── Component.tsx # Component implementation
├── lib/              # Compiled output (generated)
├── .swcrc            # SWC compiler configuration
├── package.json      # Package configuration
├── tsconfig.json     # TypeScript configuration
└── tsconfig.lib.json # TypeScript build configuration
```

### Building

The build process uses:
- **SWC** for transpiling TypeScript/JSX to JavaScript
- **TypeScript** for generating type declarations

### Features

Both components support:
- ✅ SSR (Server-Side Rendering)
- ✅ Dynamic imports with code splitting
- ✅ Theme switching (light/dark mode)
- ✅ TypeScript with full type definitions

## Environment Variables

Control which components are loaded in the main application:

```env
# Enable/disable Alerts tab
NEXT_PUBLIC_ENABLE_ALERTS_TAB=true

# Enable/disable Dashboard tab
NEXT_PUBLIC_ENABLE_DASHBOARD_TAB=true

# Enable/disable Map tab
NEXT_PUBLIC_ENABLE_MAP_TAB=true

# Enable/disable Video Management tab
NEXT_PUBLIC_ENABLE_VIDEO_MANAGEMENT_TAB=true
# Add RTSP button in Video Management tab (enabled by default, set to 'false' to hide)
NEXT_PUBLIC_VIDEO_MANAGEMENT_TAB_ADD_RTSP_ENABLE=true
```

## Troubleshooting

### Packages Not Recognized by Turbo

If turbo doesn't recognize the packages, ensure:

1. The root `package.json` includes the workspace path:
   ```json
   "workspaces": [
     "apps/*",
     "packages/*",
     "packages/nv-metropolis-bp-vss-ui/*"
   ]
   ```

2. Run `npm install` from the root to register the workspaces

3. Verify packages are linked:
   ```bash
   npm list @nv-metropolis-bp-vss-ui/alerts
   npm list @nv-metropolis-bp-vss-ui/dashboard
   npm list @nv-metropolis-bp-vss-ui/map
   ```

### Build Errors

If you encounter build errors:

1. Clean the build output:
   ```bash
   npm run clean
   ```

2. Reinstall dependencies:
   ```bash
   rm -rf node_modules
   npm install
   ```

3. Rebuild:
   ```bash
   npm run build
   ```

## Adding New Packages

To add a new package to this directory:

1. Create the package directory structure
2. Add `package.json` with proper name and scripts
3. Add `.swcrc` configuration
4. Add TypeScript configurations
5. Run `npm install` from the root
6. Build the package

