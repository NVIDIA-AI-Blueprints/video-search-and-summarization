# Configuration Management

## Purpose
The settings system provides configuration management for API endpoints, WebSocket connections, conversation data import/export, and application preferences for the NeMo Agent toolkit UI. It supports both HTTP and WebSocket communication modes with predefined schemas for different endpoint types.

## Scope
- Route(s): Modal dialog accessible from sidebar footer
- Primary components: `SettingDialog`, `Import`, `ChatbarSettings`
- External deps: Browser localStorage/sessionStorage, File API

## UI Elements

| Element | Type | Location | Action/Handler | Notes |
|--------|------|----------|----------------|-------|
| Settings Button | Button | Sidebar Footer | Opens modal | Gear icon with tooltip |
| API Endpoint Input | Input | Settings Modal | Configure base URL | HTTP chat completion endpoint |
| WebSocket URL Input | Input | Settings Modal | Configure WS URL | Real-time streaming endpoint |
| WebSocket Schema Select | Dropdown | Settings Modal | Select schema | Predefined schemas: chat_stream, chat, generate_stream, generate |
| Intermediate Steps Toggle | Toggle | Settings Modal | Enable/disable | Show AI reasoning steps during processing |
| Auto-scroll Toggle | Toggle | Settings Modal | Enable/disable | Automatic scrolling to latest messages |
| Theme Toggle | Toggle | Settings Modal | Light/Dark mode | Persisted preference |
| Import Button | Button | Settings Modal | File upload | JSON conversation import |
| Export Button | Button | Settings Modal | Download file | Export all conversations |
| Clear All Button | Button | Settings Modal | Reset data | Delete all conversations |
| Test Connection | Button | Settings Modal | Validate config | Test API connectivity |

## Component Tree
```
<ChatbarSettings>
├─ Settings Button
└─ <SettingDialog> (when open)
   ├─ <form className="settings-form">
   │  ├─ API Configuration Section
   │  │  ├─ Chat Completion URL Input
   │  │  ├─ WebSocket URL Input
   │  │  └─ WebSocket Schema Select
   │  ├─ Feature Toggles Section
   │  │  ├─ Intermediate Steps Toggle
   │  │  ├─ Expand Details Toggle
   │  │  └─ Theme Toggle
   │  └─ Data Management Section
   │     ├─ <Import />
   │     ├─ Export Button
   │     └─ Clear Conversations Button
   └─ Modal Actions (Save/Cancel)
```

## Behavior

**Settings Modal:**
- Accessible via gear icon in sidebar footer
- Modal overlay with backdrop blur
- Form validates inputs before saving
- Changes persist to browser storage

**API Configuration:**
- Input fields for HTTP and WebSocket endpoints
- Dropdown for predefined WebSocket schemas
- Test connection button validates endpoints
- Settings take effect immediately after save

**Theme Management:**
- Toggle between light and dark modes
- Theme preference persisted in localStorage
- Changes apply immediately to entire interface
- System theme detection on first visit

**Data Import/Export:**
- Export button downloads conversations as JSON
- Import accepts JSON files with validation
- Clear all removes conversations with confirmation
- Import replaces existing data completely

## Source Links
- [components/Settings/SettingDialog.tsx](../../../components/Settings/SettingDialog.tsx)
- [components/Settings/Import.tsx](../../../components/Settings/Import.tsx)
- [components/Chatbar/components/ChatbarSettings.tsx](../../../components/Chatbar/components/ChatbarSettings.tsx)