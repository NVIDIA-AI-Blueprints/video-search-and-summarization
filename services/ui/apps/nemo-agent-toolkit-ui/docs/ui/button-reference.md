# Button Reference

## Overview
This document provides a comprehensive reference for all interactive buttons used throughout the NeMo Agent toolkit UI application.

## Chat Interface Buttons

### Message Input Area

| Button | Icon | Location | Purpose | Visibility Conditions |
|--------|------|----------|---------|----------------------|
| **Voice Input** | `IconMicrophone` / `IconPlayerStopFilled` | Input field left | Start/stop voice-to-text recording | Always visible; disabled while streaming |
| **File Upload** | `IconPaperclip` | Input field right | Upload files for chat context | Currently disabled (`fileUploadEnabled: false`); hidden while streaming |
| **Send Message** | `IconSend` / Spinner | Input field right corner | Send user message | Always visible; shows spinner while streaming |
| **Remove File** | `IconTrash` | File preview area | Remove uploaded file | Only when file is uploaded |

### Chat Control Buttons

| Button | Icon | Location | Purpose | Visibility Conditions |
|--------|------|----------|---------|----------------------|
| **Stop Generating** | `IconPlayerStop` | Top center | Cancel ongoing response generation | Only visible while `messageIsStreaming` is true |
| **Regenerate Response** | `IconRepeat` | Top center | Regenerate last assistant response | Only when not streaming and conversation has >1 messages |
| **Scroll Down** | `IconArrowDown` | Bottom right | Scroll to bottom of chat | Only when `showScrollDownButton` is true |

### Message Action Buttons

| Button | Icon | Location | Purpose | Visibility Conditions |
|--------|------|----------|---------|----------------------|
| **Copy Message** | `IconCopy` / `IconCheck` | Below assistant messages | Copy message content to clipboard | Not visible while streaming; shows check mark after copy |
| **Text-to-Speech** | `IconVolume2` / `IconPlayerPause` | Below assistant messages | Play/pause message audio | Not visible while streaming; animates while playing |
| **Edit Message** | `IconEdit` | User message hover | Enable inline message editing | Only on user messages |
| **Delete Message** | `IconTrash` | User message hover | Delete message from conversation | Only on user messages |

## Sidebar Navigation Buttons

### Sidebar Controls

| Button | Icon | Location | Purpose | Visibility Conditions |
|--------|------|----------|---------|----------------------|
| **Toggle Sidebar** | `IconMenu2` | Top left/right corner | Show/hide sidebar | Position changes based on sidebar state |
| **New Chat** | `IconPlus` | Sidebar header | Create new conversation | Always visible in sidebar |
| **New Folder** | `IconFolderPlus` | Sidebar header | Create new conversation folder | Always visible in sidebar |
| **Clear Search** | `IconX` | Search input | Clear search filter | Only when search has content |

### Conversation Management

| Button | Icon | Location | Purpose | Visibility Conditions |
|--------|------|----------|---------|----------------------|
| **Select Conversation** | None | Conversation list | Switch to conversation | Always visible for each conversation |
| **Toggle Folder** | `IconChevronDown` / `IconChevronRight` | Folder header | Expand/collapse folder | Shows different icon based on folder state |

### Settings and Data Management

| Button | Icon | Location | Purpose | Visibility Conditions |
|--------|------|----------|---------|----------------------|
| **Import Data** | `IconDownload` | Sidebar footer | Import conversation data from JSON | Always visible in sidebar footer |
| **Export Data** | `IconFileExport` | Sidebar footer | Export all conversations to JSON | Always visible in sidebar footer |
| **Clear Conversations** | `IconTrash` | Sidebar footer | Delete all conversations | Only visible when conversations exist |
| **Settings** | `IconSettings` | Sidebar footer | Open application settings modal | Always visible in sidebar footer |

## Settings Modal Buttons

### Configuration Actions

| Button | Icon | Location | Purpose | Visibility Conditions |
|--------|------|----------|---------|----------------------|
| **Save Settings** | None | Modal footer | Save configuration changes | Always visible in settings modal |
| **Cancel Settings** | None | Modal footer | Close modal without saving | Always visible in settings modal |
| **Test Connection** | None | API configuration section | Validate API endpoint connectivity | Always visible in settings modal |

## Human-in-the-Loop Interaction Buttons

### Interaction Modal

| Button | Icon | Location | Purpose | Visibility Conditions |
|--------|------|----------|---------|----------------------|
| **Submit Text** | None | Interaction modal | Submit text input for workflow | When text input is required |
| **Submit Choice** | None | Interaction modal | Submit selected option | When choice selection is required |
| **Close Modal** | `IconX` | Modal header | Close interaction modal | Always visible in interaction modal |
| **Choice Option** | None | Modal body | Select from multiple choices | When multiple choice interaction is required |

## Markdown Content Buttons

### Code Block Actions

| Button | Icon | Location | Purpose | Visibility Conditions |
|--------|------|----------|---------|----------------------|
| **Copy Code** | `IconCopy` | Code block header | Copy code content to clipboard | Always visible on code blocks |
| **Download Code** | `IconDownload` | Code block header | Download code as file | Always visible on code blocks |

### Image Interactions

| Button | Icon | Location | Purpose | Visibility Conditions |
|--------|------|----------|---------|----------------------|
| **Toggle Fullscreen** | None | Image overlay | Enter/exit fullscreen view | Always visible on images |

### Chart Actions

| Button | Icon | Location | Purpose | Visibility Conditions |
|--------|------|----------|---------|----------------------|
| **Download Chart** | `IconDownload` | Chart component | Download chart as image | Always visible on charts |

## Implementation Notes

### Button States
- **Disabled**: Buttons are disabled during streaming or when actions are not applicable
- **Loading**: Some buttons show spinner animations during processing
- **Active**: Certain buttons have active states (e.g., recording, playing audio)

### Accessibility
- All buttons include appropriate `aria-label` attributes for screen readers
- Keyboard navigation is supported with Tab and Enter keys
- Focus indicators are provided for keyboard users

### Styling Patterns
- Primary actions use brand colors (`#76b900`)
- Destructive actions use red colors for delete operations
- Hover states provide visual feedback
- Dark mode support with appropriate color variations

## Related Documentation
- [Chat Interface](./chat/chat-interface.md) - Detailed chat functionality
- [Sidebar Navigation](./sidebar/conversation-management.md) - Conversation management
- [Configuration Management](./settings/configuration-management.md) - Settings and preferences