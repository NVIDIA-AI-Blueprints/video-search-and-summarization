# VST UI TypeScript

A professional-grade web application for video streaming and sensor data management, built with React, TypeScript, and Vite. This application provides a robust interface for sensor management, video streaming, and data recording capabilities.

## Overview

The VST UI TypeScript application is designed for enterprise-level video streaming and sensor data management. It offers comprehensive tools for monitoring, configuring, and managing sensor data streams with real-time visualization capabilities.

## Key Features

- Video streaming and playback management
- Sensor data visualization and configuration
- Real-time data monitoring and analysis
- Configurable sensor settings and parameters
- Theme customization (Light/Dark mode)
- Responsive and adaptive interface
- Advanced debugging and logging capabilities
- Integrated file management system

## Technology Stack

- **Core Framework**: React 18
- **Language**: TypeScript
- **Build System**: Vite
- **UI Framework**: Material-UI (MUI)
- **State Management**: Zustand
- **Routing**: React Router DOM
- **Data Visualization**: ApexCharts, D3
- **Styling**: SASS/SCSS, Emotion
- **Desktop Integration**: Electron

## System Requirements

- Node.js (LTS version)
- npm or yarn package manager
- Modern web browser with JavaScript enabled

## Installation

1. Clone the repository:
```bash
git clone [repository-url]
cd vst-ui-ts
```

2. Link with vst-web-streamer:
```bash
npm run install:link
```

This will:
- Clone the vst-web-streamer repository if not present
- Install its dependencies
- Build the package
- Create a symlink for local development

## Development

### Web Application
```bash
# Development server
npm run dev

# Production build
npm run build

# Preview production build
npm run preview
```

### Desktop Application (Experimental)
```bash
# Development mode
npm run electron:dev

# Production build
npm run electron:build
```

## Build Commands

| Command | Description |
|---------|-------------|
| `npm run dev` | Start development server |
| `npm run build` | Create production build |
| `npm run build:analyze` | Build with bundle analysis |
| `npm run build:compressed` | Create compressed production build |
| `npm run build:all` | Build both standard and compressed versions |
| `npm run lint` | Execute ESLint validation |
| `npm run preview` | Preview production build |
| `npm run electron:dev` | Launch electron development environment |
| `npm run electron:build` | Build electron application |
| `npm run install:link` | Link with vst-web-streamer package |

## Project Architecture

```
src/
├── assets/         # Static resources
├── components/     # Reusable UI components
├── features/       # Feature-specific modules
├── hooks/         # Custom React hooks
├── interfaces/    # TypeScript type definitions
├── layout/        # Layout components
├── pages/         # Application pages
├── services/      # API integrations
├── theme/         # Theme configuration
└── utils/         # Utility functions
```

## Configuration

The application configuration is managed through `src/config.tsx`, supporting:

- Sensor management endpoints
- Stream recorder endpoints
- Storage management endpoints
- Live stream endpoints
- Replay stream endpoints
- Debug logging configuration

## Development Guidelines

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/feature-name`)
3. Implement changes
4. Commit with descriptive messages
5. Push to the branch
6. Submit a Pull Request

### Code Style and Quality

This project uses ESLint and Prettier for code quality and consistent formatting. The following configuration is enforced:

#### ESLint Configuration
```javascript
module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  ignorePatterns: ['dist', '.eslintrc.cjs', 'electron-main.js'],
  parser: '@typescript-eslint/parser',
  plugins: ['react-refresh'],
  rules: {
    'react-refresh/only-export-components': [
      'warn',
      { allowConstantExport: true },
    ],
    "no-use-before-define": "off",
    "@typescript-eslint/no-use-before-define": "off",
    "react-refresh/only-export-components": ["warn", { "allowConstantExport": true }],
    "react-hooks/exhaustive-deps": "off"
  },
}
```

#### Code Quality Checks
- Run ESLint validation: `npm run lint`
- Ensure all tests pass before submitting PRs
- Follow TypeScript best practices
- Maintain consistent code formatting
- Write meaningful commit messages

## License

This project is licensed under the Apache License 2.0. See the LICENSE file for details.

## Support

For technical support or inquiries, please contact the development team at [support-email].

# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react/README.md) uses [Babel](https://babeljs.io/) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type aware lint rules:

- Configure the top-level `parserOptions` property like this:

```js
export default {
  // other rules...
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    project: ['./tsconfig.json', './tsconfig.node.json'],
    tsconfigRootDir: __dirname,
  },
}
```

- Replace `plugin:@typescript-eslint/recommended` to `plugin:@typescript-eslint/recommended-type-checked` or `plugin:@typescript-eslint/strict-type-checked`
- Optionally add `plugin:@typescript-eslint/stylistic-type-checked`
- Install [eslint-plugin-react](https://github.com/jsx-eslint/eslint-plugin-react) and add `plugin:react/recommended` & `plugin:react/jsx-runtime` to the `extends` list
