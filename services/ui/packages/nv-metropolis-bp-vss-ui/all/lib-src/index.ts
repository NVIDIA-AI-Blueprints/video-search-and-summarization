// SPDX-License-Identifier: MIT
// Re-export all components from nv-metropolis-bp-vss-ui packages
export { AlertsComponent, Controls as AlertsControls, CreateAlertRulesView } from '@nv-metropolis-bp-vss-ui/alerts';
export type {
  AlertsComponentProps,
  AlertsSidebarControlHandlers,
  AlertsView,
  AlertRulesType,
  RealtimeAlertRule,
  RealtimeAlertRuleDraft,
} from '@nv-metropolis-bp-vss-ui/alerts';

export { SearchComponent, SearchSidebarControls } from '@nv-metropolis-bp-vss-ui/search';
export type { SearchComponentProps, SearchSidebarControlHandlers, QueryDataContext } from '@nv-metropolis-bp-vss-ui/search';

export { DashboardComponent, DashboardSidebarControls } from '@nv-metropolis-bp-vss-ui/dashboard';
export type { DashboardComponentProps, DashboardSidebarControlHandlers } from '@nv-metropolis-bp-vss-ui/dashboard';

export { MapComponent, MapSidebarControls } from '@nv-metropolis-bp-vss-ui/map';
export type { MapComponentProps, MapSidebarControlHandlers } from '@nv-metropolis-bp-vss-ui/map';

export { VideoManagementComponent } from '@nv-metropolis-bp-vss-ui/video-management';
export type {
  VideoManagementComponentProps,
  VideoManagementSidebarControlHandlers,
  VideoManagementData,
  ChatSidebarQueryContext,
} from '@nv-metropolis-bp-vss-ui/video-management';
