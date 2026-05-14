<!-- SPDX-License-Identifier: MIT -->
# Nemo Agent Toolkit UI Monorepo

This is the monorepo for the Nemo Agent Toolkit UI and other apps (example: VSS Blueprints Agentic UI) that are built on top of it.

This is forked from the original [NeMo Agent Toolkit UI](https://github.com/NVIDIA/NeMo-Agent-Toolkit-UI) repository.

## Node version (nvm)

This project uses the Node version specified in `.nvmrc`. With [nvm](https://github.com/nvm-sh/nvm) installed:

```bash
# Install and use the Node version from .nvmrc
nvm install
nvm use
```

If you don't have nvm yet, install it, then run the commands above:

```bash
# Install nvm (typically run the install script from https://github.com/nvm-sh/nvm#installing-and-updating)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
# Restart your shell or source ~/.bashrc / ~/.zshrc, then:
nvm install
nvm use
```

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
```

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

To do a full production build (all packages and the app) and then run the production Next standalone server, run from repo root.

For **`nv-metropolis-bp-vss-ui`**, the app splits **`next build`** (`build`) from copying static assets and `public/` into the standalone output (`bundle`). Run both Turbo tasks:

```bash
npx turbo build --filter=./packages/** \
  && npx turbo run build bundle --filter=./apps/nv-metropolis-bp-vss-ui \
  && npx turbo start --filter=./apps/nv-metropolis-bp-vss-ui
```

For **`nemo-agent-toolkit-ui`**, bundling those assets is already part of the app `build` script, so only `build` is required:

```bash
npx turbo build --filter=./packages/** \
  && npx turbo build --filter=./apps/nemo-agent-toolkit-ui \
  && npx turbo start --filter=./apps/nemo-agent-toolkit-ui
```

**Possible app names:** `nemo-agent-toolkit-ui`, `nv-metropolis-bp-vss-ui`

Note: Root `npm run build` runs `turbo run build` only (no `bundle`). Use the commands above—or CI’s `npx turbo run build bundle`—when you need a runnable standalone tree for **`nv-metropolis-bp-vss-ui`**.

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

<!-- ci-trigger: no-op edit to exercise the VSS_CHANGED_UI bucket (services/ui/**) in metromind/ci-vss-oss vss-diff-probe. Safe to delete. -->
