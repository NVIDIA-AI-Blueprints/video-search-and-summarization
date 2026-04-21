<!-- SPDX-License-Identifier: MIT -->
# Nemo Agent Toolkit UI Monorepo

This is the monorepo for the Nemo Agent Toolkit UI and other apps (example: VSS Blueprints Agentic UI) that are built on top of it.

This is forked from the original [NeMo Agent Toolkit UI](https://github.com/NVIDIA/NeMo-Agent-Toolkit-UI) repository.

## Getting Started

```bash
npm install
# verify turbo is installed
npx turbo --version
```

### Build packages
```bash
# Install dependencies for all packages (turbo does not handle dependency installation, use npm or pnpm)
npm install

# Then build all packages
npx turbo build --filter=./packages/**

To get a list of packages, run:
```bash
npx turbo list --filter=./packages/**
```

To get a list of apps, run:
```bash
npx turbo list --filter=./apps/*
```

### Run applications in dev mode

Run a single application in dev mode:
```bash
# replace <APP_NAME> with the name of the application you want to run
npx turbo dev --filter=./apps/<APP_NAME>
# npx turbo dev --filter=./apps/nemo-agent-toolkit-ui
```

Run all applications in parallel in dev mode:
```bash
npx turbo dev --filter=./apps/*
```

### Full production build and run production server

To do a full production build (all packages and the app) and then run the production Next server, run from repo root:

```bash
npx turbo build --filter=./packages/** && npx turbo build --filter=./apps/<APP_NAME> && npx turbo start --filter=./apps/<APP_NAME>
```

Replace `<APP_NAME>` with the app you want to run. This builds all packages, builds the app, then starts the production server (`next start`).

**Possible app names:** `nemo-agent-toolkit-ui`, `nv-metropolis-bp-vss-ui`

## Testing

This monorepo uses Jest for testing. You can run tests for all packages/apps or target specific ones.

### Run tests for all packages and apps

```bash
# Run all tests
npm test

# Or using turbo directly
npx turbo run test

# Show only summary (hide individual test output)
npx turbo run test 2>&1 | grep -E "(Test Suites:|Tests:|Tasks:|Cached:|FAIL )"
```

### Run tests for a specific package

```bash
# By package name
npx turbo run test --filter=<PACKAGE_NAME>

# By path
npx turbo run test --filter=./packages/<path-to-package>

# Example: VSS search package
npx turbo run test --filter=@nv-metropolis-bp-vss-ui/search
```

### Run tests for a specific app or package

```bash
npx turbo run test --filter=<PACKAGE_NAME>
# Or by path: npx turbo run test --filter=./packages/<path-to-package>

# Example (package that has tests)
npx turbo run test --filter=@nv-metropolis-bp-vss-ui/video-management
```

### Run tests with watch mode

```bash
cd packages/<path-to-package> && npm run test:watch

# Example
cd packages/nv-metropolis-bp-vss-ui/search && npm run test:watch
```

### Run tests with coverage

```bash
cd packages/<path-to-package> && npm run test:coverage

# Example
cd packages/nv-metropolis-bp-vss-ui/search && npm run test:coverage
```

### Adding New Tests

Sample test files are provided as boilerplate/reference code:

- **Search Tab**: `packages/nv-metropolis-bp-vss-ui/search/__tests__/SearchComponent.test.tsx`
- **Alerts Tab**: `packages/nv-metropolis-bp-vss-ui/alerts/__tests__/AlertsComponent.test.tsx`
- **Video Management**: `packages/nv-metropolis-bp-vss-ui/video-management/__tests__/utils/filterStreams.test.ts`

These files demonstrate:
- Basic component rendering tests
- Props validation tests
- Conditional rendering tests
- Callback prop testing patterns
- Mocking external dependencies (hooks, components, APIs)

To add new tests:
1. Create test files in `__tests__/` directory following the naming pattern `*.test.tsx` or `*.test.ts`
2. Use React Testing Library for rendering and assertions
3. Mock external dependencies using `jest.mock()`
4. Follow the patterns shown in the sample test files

## Third-party dependency source archive

To create a timestamped tarball of 3rd-party dependency **source for production only** (no devDependencies)—i.e. only the dependencies used to build and run the production Docker image—run from the repo root:

```bash
./create-third-party-deps-tar.sh
```

The script copies the repo to a temporary directory, runs `npm ci --omit=dev`, then archives the resulting `node_modules` from root and all workspaces. Output is `third-party-deps-sources-YYYYMMDD-HHMMSS.tar.gz` in the project root (for license/source compliance).
