# Sidebar Components

## Purpose
Sidebar components provide a reusable, generic sidebar container with collapsible functionality, search, and drag-and-drop support for organizing content like conversations and folders.

## Components

### Sidebar
Main container component that provides a flexible sidebar layout with search, items, folders, and footer sections.

### SidebarButton
Reusable button component with icon and text for sidebar actions.

### OpenSidebarButton / CloseSidebarButton
Toggle buttons for opening and closing the sidebar, positioned based on sidebar state.

## Behavior

**Layout and Positioning:**
- Responsive width (270px desktop, full-width mobile)
- Fixed positioning with appropriate z-index layering
- Collapsible with smooth open/close animations
- Adapts button positions based on sidebar state

**Content Organization:**
- Search integration with live filtering
- Separate sections for items and folders
- Optional footer component support
- Drag and drop support for item organization