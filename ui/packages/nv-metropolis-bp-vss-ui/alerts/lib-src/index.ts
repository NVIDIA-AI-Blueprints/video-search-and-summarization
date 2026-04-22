// SPDX-License-Identifier: MIT
/**
 *  Alerts Module Entry Point - Public API Exports
 * 
 * This file serves as the main entry point for the alerts management module, providing
 * a clean and organized public API for external consumption. It exports all public
 * components, hooks, types, and utilities that are intended for use by other parts
 * of the application or external packages.
 * 
 * **Exported Components:**
 * - AlertsComponent: Main alerts management interface with comprehensive filtering and display
 * - AlertsSidebarControls: Simplified controls for external sidebar rendering
 * - Supporting components available through the main component's internal architecture
 *
 */

export { AlertsComponent } from './AlertsComponent';
export type { AlertsComponentProps } from './AlertsComponent';
export { AlertsSidebarControls } from './components/AlertsSidebarControls';
export type { AlertsSidebarControlHandlers } from './types';