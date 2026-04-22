# Folder

## Purpose
A collapsible folder component for organizing conversations and other items with drag-and-drop support, inline editing, and deletion functionality.

## Behavior

**Folder State Management:**
- Maintains expanded/collapsed state locally
- Click folder header to toggle open/closed
- Visual indicators show current state (caret icons)
- Auto-expands when items are dropped onto folder

**Editing and Deletion:**
- Double-click or edit button enables inline renaming
- Enter key confirms rename operation
- Delete button shows confirmation before removal
- Escape key cancels editing without saving changes

**Drag and Drop:**
- Visual feedback during drag operations (background highlight)
- Accepts dropped items and calls parent handler
- Auto-opens folder when items are dragged over
- Removes visual feedback when drag leaves area

**Visual States:**
- Hover states for interactive elements
- Active editing state with input field
- Loading/processing states during operations
- Error states for failed operations