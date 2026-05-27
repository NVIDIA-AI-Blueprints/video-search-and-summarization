# NvCompositor Custom Grid Layout Implementation

## Overview
This document describes the enhanced NvCompositor class that now supports user-defined grid layouts while maintaining 100% backward compatibility with existing functionality.

## New Features

### 1. Data Structures

#### TileSpacing
Represents spacing configuration:
```cpp
struct TileSpacing {
    int horizontal = 1;    // Horizontal spacing in pixels
    int vertical = 1;      // Vertical spacing in pixels
};
```

#### GridTile
Represents a single tile in the grid layout:
```cpp
struct GridTile {
    std::string id = "";          // Unique tile identifier
    std::string userId = "";      // User identifier for this tile
    int row = 0;                  // Grid row position (0-based)
    int column = 0;               // Grid column position (0-based)
    int width = 1;                // Tile width in grid units
    int height = 1;               // Tile height in grid units
    std::string videoUrl = "";    // Video URL for this tile
    
    // Calculated position values (percentages for rendering)
    int topPercent = 0;           // Top position percentage
    int leftPercent = 0;          // Left position percentage
    int widthPercent = 0;         // Width percentage
    int heightPercent = 0;        // Height percentage
};
```

#### GridLayout
Represents the complete grid configuration:
```cpp
struct GridLayout {
    int columns = 0;                   // Number of columns
    int rows = 0;                      // Number of rows
    TileSpacing tileSpacing;           // Spacing configuration
    std::vector<GridTile> tiles;       // Tile definitions
    bool isCustom = false;             // Flag indicating custom layout
    
    void calculateTilePositions();     // Calculate percentage positions
};
```

### 2. New Constructor
```cpp
NvCompositor(std::vector<string> urls_list, const GridLayout& customLayout);
```
Creates a compositor with a predefined custom layout.

### 3. New Methods

#### Layout Management
```cpp
void setCustomGridLayout(const GridLayout& layout);
void setGridLayoutFromJson(const std::string& jsonConfig);
void setGridLayoutFromPrompt(const std::string& aiPrompt);
GridLayout getCurrentLayout() const;
```

## Usage Examples

### 1. Programmatic Layout Creation
```cpp
std::vector<std::string> urls = {"rtsp://stream1", "rtsp://stream2"};

// Create 2x1 side-by-side layout
GridLayout layout(2, 1, TileSpacing(5, 5)); // 2 cols, 1 row, 5px spacing
layout.isCustom = true;
layout.tiles.push_back(GridTile("left", "user1", 0, 0, 1, 1, "rtsp://stream1"));
layout.tiles.push_back(GridTile("right", "user2", 0, 1, 1, 1, "rtsp://stream2"));
layout.calculateTilePositions();

NvCompositor compositor(urls, layout);
```

### 2. JSON Configuration
```cpp
std::string jsonConfig = R"({
    "columns": 2,
    "rows": 2,
    "tileSpacing": {
        "horizontal": 5,
        "vertical": 5
    },
    "tiles": [
        {
            "id": "tile1",
            "userId": "user1",
            "row": 0,
            "column": 0,
            "width": 1,
            "height": 1,
            "videoUrl": "rtsp://stream1"
        },
        {
            "id": "tile2",
            "userId": "user2",
            "row": 0,
            "column": 1,
            "width": 1,
            "height": 1,
            "videoUrl": "rtsp://stream2"
        },
        {
            "id": "tile3",
            "userId": "user3",
            "row": 1,
            "column": 0,
            "width": 1,
            "height": 1,
            "videoUrl": "rtsp://stream3"
        },
        {
            "id": "tile4",
            "userId": "user4",
            "row": 1,
            "column": 1,
            "width": 1,
            "height": 1,
            "videoUrl": "rtsp://stream4"
        }
    ]
})";

NvCompositor compositor(urls);
compositor.setGridLayoutFromJson(jsonConfig);
```

### 3. AI Prompt-Based Configuration
```cpp
NvCompositor compositor(urls);

// Supported prompts:
compositor.setGridLayoutFromPrompt("Create a 2x2 grid layout with 5 pixel spacing");
compositor.setGridLayoutFromPrompt("Set up picture-in-picture with main window and small overlay");
compositor.setGridLayoutFromPrompt("Arrange 4 streams in a quad layout");
compositor.setGridLayoutFromPrompt("Side by side layout for 2 streams");
```

## AI Prompt Patterns

The system recognizes these natural language patterns:

### Grid Patterns
- `"2x2"`, `"3x3"`, `"4x2"` - Extracts dimensions
- `"spacing: 5"`, `"spacing 10"` - Extracts spacing value

### Special Layouts
- `"main"` + `"pip"` → Picture-in-Picture layout
- `"side by side"` → Horizontal split layout  
- `"quad"` → 2x2 equal grid
- Default → Regular grid based on dimensions

## JSON Schema

The implementation follows this JSON schema structure:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Video Grid Layout",
  "type": "object",
  "properties": {
    "columns": { 
      "type": "integer", 
      "minimum": 1,
      "description": "Number of columns in the grid"
    },
    "rows": { 
      "type": "integer", 
      "minimum": 1,
      "description": "Number of rows in the grid"
    },
    "tileSpacing": {
      "type": "object",
      "properties": {
        "horizontal": { 
          "type": "integer", 
          "description": "Horizontal spacing in pixels" 
        },
        "vertical": { 
          "type": "integer", 
          "description": "Vertical spacing in pixels" 
        }
      },
      "required": ["horizontal", "vertical"],
      "description": "Spacing between tiles"
    },
    "tiles": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": { 
            "type": "string",
            "description": "Unique tile identifier"
          },
          "userId": { 
            "type": "string",
            "description": "User identifier for this tile"
          },
          "row": { 
            "type": "integer",
            "minimum": 0,
            "description": "Grid row position (0-based)"
          },
          "column": { 
            "type": "integer",
            "minimum": 0,
            "description": "Grid column position (0-based)"
          },
          "width": { 
            "type": "integer",
            "minimum": 1,
            "description": "Tile width in grid units"
          },
          "height": { 
            "type": "integer",
            "minimum": 1,
            "description": "Tile height in grid units"
          },
          "videoUrl": { 
            "type": "string",
            "description": "Video URL for this tile"
          }
        },
        "required": ["id", "userId", "row", "column", "videoUrl"]
      }
    }
  },
  "required": ["columns", "rows", "tiles", "tileSpacing"]
}
```

## New Schema Benefits

The updated JSON schema provides several advantages:

### Enhanced Tile Management
- **Unique Identifiers**: Each tile has an `id` for precise control
- **User Association**: `userId` field enables user-specific tile management
- **Video URL Mapping**: Direct `videoUrl` association with each tile
- **Flexible Sizing**: Tiles can span multiple grid units with `width` and `height`

### Improved Spacing Control
- **Separate Spacing**: Independent horizontal and vertical spacing control
- **Pixel-Perfect Layout**: Precise pixel-based spacing configuration
- **Better Visual Separation**: Enhanced tile boundaries and spacing

### Grid-Based Positioning
- **Logical Coordinates**: Row/column positioning instead of percentage-based
- **Scalable Design**: Easy to modify grid dimensions without recalculating positions
- **Asymmetric Layouts**: Support for tiles of different sizes within the same grid

### Validation and Error Handling
- **Schema Validation**: Built-in validation for all required fields
- **Bounds Checking**: Automatic validation of tile positions within grid bounds
- **Size Validation**: Ensures tiles don't exceed grid boundaries

## Backward Compatibility

- **All existing code continues to work unchanged**
- **Default constructor behavior is identical**
- **Existing layout algorithms are preserved**
- **No performance impact for existing usage**
- **AI prompt parsing still supported** (generates tiles internally)

## Architecture Changes

### NvCompositor Class Extensions
- New member variables for grid layout storage
- Thread-safe layout management with mutex protection
- Layout calculation methods separated from composition logic

### NvBufWrapper Enhancements
- Overloaded `doComposition()` method accepts custom layout
- Backward compatible - existing calls work unchanged
- Custom layout bypasses default positioning logic

### Layout Calculation Flow
```
1. Check if custom layout is set (m_gridLayout.isCustom)
2. If custom: Use calculateCustomLayout()
3. If default: Use calculateDefaultLayout() 
4. Pass layout to NvBufWrapper::doComposition()
5. NvBufWrapper applies layout during composition
```

## Thread Safety

- All layout operations are protected by `m_gridLayoutLock`
- Layout changes are atomic and thread-safe
- No interference with existing compositor thread

## Error Handling

- Invalid JSON gracefully falls back to default layout
- Malformed AI prompts use sensible defaults
- Layout validation prevents buffer overflows
- Comprehensive logging for debugging

## Performance Considerations

- Custom layout calculation: O(n) where n = number of streams
- JSON parsing: One-time cost during configuration
- AI prompt parsing: Lightweight regex operations
- No performance impact when using default layouts

## Integration Points

### Pipeline Builder Integration
```cpp
// In CompositePipelineBuilder::buildCompositorPipeline()
const auto& compositor = config.getCompositor();

if (!compositor.gridLayoutJson.empty()) {
    m_compositor->setGridLayoutFromJson(compositor.gridLayoutJson);
} else if (!compositor.gridLayoutPrompt.empty()) {
    m_compositor->setGridLayoutFromPrompt(compositor.gridLayoutPrompt);
}
```

### Configuration File Integration
Add to `PipelineConfiguration::CompositorConfig`:
```cpp
struct CompositorConfig {
    // ... existing fields ...
    std::string gridLayoutJson = "";
    std::string gridLayoutPrompt = "";
    bool useCustomLayout = false;
};
```

## Testing

### Unit Tests Required
- GridCell and GridLayout construction
- JSON parsing with valid/invalid inputs
- AI prompt parsing for all supported patterns
- Layout calculation accuracy
- Thread safety of layout operations
- Backward compatibility verification

### Integration Tests Required  
- End-to-end composition with custom layouts
- Dynamic layout changes during operation
- Performance comparison with default layouts
- Memory leak detection
- Multi-stream composition accuracy

## Future Enhancements

1. **Advanced Layout Patterns**
   - Asymmetric grids
   - Overlapping cells
   - Animated transitions

2. **Enhanced AI Prompts**
   - More natural language patterns
   - Layout templates by name
   - Voice command integration

3. **Visual Layout Editor**
   - Web-based layout designer
   - Real-time preview
   - Template library

4. **Dynamic Adaptation**
   - Auto-layout based on stream count
   - Responsive layouts for different resolutions
   - Smart positioning algorithms

## Troubleshooting

### Common Issues

**Layout not applied:**
- Check `isCustom` flag is set to `true`
- Verify cell count matches stream count
- Ensure positions are within 0-100 range

**JSON parsing fails:**
- Validate JSON syntax
- Check required fields are present
- Review error logs for specific issues

**AI prompt not recognized:**
- Use supported pattern keywords
- Check spelling of layout terms
- Fall back to JSON for complex layouts

### Debug Logging
Enable detailed logging to track layout application:
```cpp
LOG(info) << "Custom grid layout set: " << layout.rows << "x" << layout.cols;
LOG(info) << "Using custom grid layout with " << cells.size() << " cells";
```

## Conclusion

The enhanced NvCompositor provides powerful, flexible grid layout capabilities while maintaining complete backward compatibility. The implementation follows best practices for thread safety, error handling, and performance, making it suitable for production use in demanding video composition scenarios.
