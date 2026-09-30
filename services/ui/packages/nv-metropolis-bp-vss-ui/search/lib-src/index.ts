// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 *  Search Module Entry Point - Public API Exports
 * 
 * This file serves as the main entry point for the search management module, providing
 * a clean and organized public API for external consumption. It exports all public
 * components, hooks, types, and utilities that are intended for use by other parts
 * of the application or external packages.
 * 
 * **Exported Components:**
 * - SearchComponent: Main search management interface with comprehensive filtering and display
 * - SearchSidebarControls: Simplified controls for external sidebar rendering
 * - Supporting components available through the main component's internal architecture
 *
 */

export { SearchComponent } from './SearchComponent';
export type { SearchComponentProps } from './SearchComponent';
export { SearchSidebarControls } from './components/SearchSidebarControls';
export type { SearchSidebarControlHandlers, QueryDataContext } from './types';