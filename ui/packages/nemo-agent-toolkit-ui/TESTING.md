# Testing Guide

This document outlines the testing setup and best practices for the WebSocket/HTTP chat implementation.

### Installation

```bash
npm install
```

## Running Tests

### Basic Commands

```bash
# Run all tests
npm run test

# Run tests in watch mode
npm run test:watch

# Run tests with coverage
npm run test:coverage

# Run tests for CI (no watch, with coverage)
npm run test:ci
```

### Debug Commands

```bash
# Run specific test file
npm test -- Chat.test.tsx

# Run tests matching pattern
npm test -- --testNamePattern="WebSocket"

# Run with verbose output
npm test -- --verbose

# Run without coverage (faster)
npm test -- --no-coverage
```
