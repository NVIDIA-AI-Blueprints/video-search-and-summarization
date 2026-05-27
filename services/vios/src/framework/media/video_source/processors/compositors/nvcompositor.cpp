/*
 * SPDX-FileCopyrightText: Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "nvcompositor.h"
#include "logger.h"
#include <algorithm>
#include <sstream>

// Default grid layout constants for compositor
static constexpr int DEFAULT_GRID_COLS = 3;
static constexpr int DEFAULT_GRID_ROWS = 1;
static constexpr int DEFAULT_SCALING_FACTOR = 2;

// Constructor that extracts URLs from GridLayout
NvCompositor::NvCompositor(const GridLayout& gridLayout)
: IMediaDataConsumer("NvCompositor"), m_gridLayout(gridLayout)
{
    // Extract video URLs from grid layout tiles
    for (const auto& tile : m_gridLayout.tiles)
    {
        if (!tile.videoUrl.empty())
        {
            m_urlsList.push_back(tile.videoUrl);
            LOG(warning) << "URLs for Composition :" << tile.videoUrl << " (tile: " << tile.id << ")" << endl;
            m_streamIDList.push_back(getStreamIdFromUrl(tile.videoUrl, "/live/"));
        }
    }
    
    // Validate that we have URLs to work with
    if (m_urlsList.empty())
    {
        LOG(error) << "No valid video URLs found in GridLayout tiles!" << endl;
        throw std::invalid_argument("GridLayout must contain tiles with valid video URLs");
    }
    
    m_surfacePool = std::make_shared<NvSurfacePool>();
    m_compositorThread = std::thread(&NvCompositor::doCompositeTask, this);
    
    LOG(info) << "NvCompositor created with grid layout: " << m_gridLayout.rows 
              << "x" << m_gridLayout.columns << " with " << m_gridLayout.tiles.size() 
              << " tiles and " << m_urlsList.size() << " video URLs" << endl;
}

NvCompositor::~NvCompositor ()
{
    try {
        LOG(info) << "Entry " << __METHOD_NAME__ <<  endl;

        pushEmptyFrame ();
        if (m_compositorThread.joinable())
        {
            LOG(error) << "Waiting for compose samples thread join" << endl;
            m_compositorThread.join();
        }
        m_surfacePool->freeSurfacesAndDataStructure(false);
        m_surfacePool.reset();
        LOG(info) << "Exit " << __METHOD_NAME__ <<  endl;
    } catch (const std::exception& e) {
        try { LOG(error) << "Exception in ~NvCompositor: " << e.what() << endl; } catch (...) { (void)std::current_exception(); }
    } catch (...) {
        try { LOG(error) << "Unknown exception in ~NvCompositor" << endl; } catch (...) { (void)std::current_exception(); }
    }
}

void NvCompositor::setFrameRate(string frame_rate)
{
    m_frameRate = stringToDouble(frame_rate);
}

void NvCompositor::setOriginalFrameSize()
{
    if (m_consumer)
    {
        m_consumer->setOriginalFrameSize();
    }
}

void NvCompositor::setTargetFrameSize (FrameSize &target_size)
{
    std::lock_guard<std::mutex> lock(m_targetFrameSizeLock);
    m_targetFrameSize = target_size;
}

void NvCompositor::setConsumer(std::shared_ptr<IMediaDataConsumer> consumer)
{
    m_consumer = consumer;
    setConsumerType (compositor);
}

void NvCompositor::updateGstMap(std::shared_ptr<RawFrameParams> frame_data)
{
    /* Get the buffer from sample */
    if (frame_data->m_sample)
    {
        frame_data->m_gstBuffer = gst_sample_get_buffer (frame_data->m_sample);
        if (frame_data->m_gstBuffer == nullptr)
        {
            LOG (warning) << "No more buffers available from app sink element" << endl;
            gst_sample_unref (frame_data->m_sample);
            return;
        }

        /* Map the gst buffer */
        if (gst_buffer_map (frame_data->m_gstBuffer, &frame_data->m_map, GST_MAP_READ) == false)
        {
            LOG (warning) << "Map the gst buffer Failed" << endl;
            gst_sample_unref (frame_data->m_sample);
            return;
        }
    }
}

void NvCompositor::onFrame(std::shared_ptr<RawFrameParams> frame_data)
{
    std::shared_ptr<RawFrameParams> compositor_frame_data = std::static_pointer_cast<RawFrameParams>(frame_data);
    if (frame_data->m_sample)
    {
        gst_sample_ref ((GstSample *)frame_data->m_sample);
    }
    std::lock_guard<std::mutex> queueLock(m_queueLock);
    m_queue.push(compositor_frame_data);
    m_flowData = true;
    m_condVar.notify_all();
}

void NvCompositor::pushEmptyFrame ()
{
    std::lock_guard<std::mutex> queueLock(m_queueLock);
    std::shared_ptr<RawFrameParams> empty_frame = make_shared<RawFrameParams>();
    m_queue.push(empty_frame);
    m_flowData = true;
    m_condVar.notify_all();
}

void NvCompositor::doCompositeTask()
{
    LOG(warning) << "Compositor thread created" << endl;
    uint32_t target_width = 1920;
    uint32_t target_height = 1080;
    std::map <string, shared_ptr<RawFrameParams>, std::less<>> nv_buffer_list;
    
    // Frame timing control
    const auto frame_interval = std::chrono::microseconds((uint64_t)(1000000/m_frameRate));


    auto next_frame_time = std::chrono::steady_clock::now();
    uint64_t frame_count = 0;
    auto fps_check_time = next_frame_time;

    while(m_stop == false)
    {
        auto current_time = std::chrono::steady_clock::now();

        // Process queue with timeout until next frame is due
        {
            std::unique_lock<std::mutex> lk(m_queueLock);
            m_condVar.wait_until(lk, next_frame_time, [this]
            {
                return !m_queue.empty() || m_stop;
            });

            // Process all available frames
            while (!m_queue.empty())
            {
                auto sink_frame = m_queue.front();
                m_queue.pop();

                if (sink_frame->m_sourceHeight == 0 && sink_frame->m_sourceWidth == 0)
                {
                    LOG(error) << "Received empty frame, breaking loop" << endl;
                    m_stop = true;
                    break;
                }

                if (sink_frame != nullptr)
                {
                    string stream_id = sink_frame->m_streamId;
                    auto it = nv_buffer_list.find(stream_id);
                    if (it != nv_buffer_list.end())
                    {
                        if (it->second && it->second->m_sample)
                        {
                            gst_sample_unref(it->second->m_sample);
                        }
                        nv_buffer_list.erase(it);
                    }
                    updateGstMap(sink_frame);
                    nv_buffer_list[stream_id] = sink_frame;
                }
            }
        }

        // Check if it's time for next frame
        if (current_time >= next_frame_time && nv_buffer_list.size() > 0)
        {
            {
                std::lock_guard<std::mutex> lock(m_targetFrameSizeLock);
                target_width = m_targetFrameSize.m_width;
                target_height = m_targetFrameSize.m_height;
            }

            FD_Index_Pair fd_index_pair = m_surfacePool->getFreeFd(
                false, m_sourceFrameSize.m_width, m_sourceFrameSize.m_height, true);
            
            if (fd_index_pair.first > 0)
            {
                // Create layout rectangles array for custom layout
                size_t buffer_count = nv_buffer_list.size();
                NvBufSurfTransformRect* dstCompRect = nullptr;
                
                // Check if we should use custom layout
                if (m_gridLayout.isCustom && !m_gridLayout.tiles.empty()) {
                    dstCompRect = new NvBufSurfTransformRect[buffer_count];
                    calculateCustomLayout(dstCompRect, buffer_count, target_width, target_height);
                    
                    NvBufWrapper::getInstance()->doComposition(
                        &fd_index_pair.first, 
                        nv_buffer_list, 
                        m_sourceFrameSize.m_width, 
                        m_sourceFrameSize.m_height, 
                        dstCompRect, 
                        buffer_count);
                        
                    delete[] dstCompRect;
                } else {
                    // Use default layout
                    NvBufWrapper::getInstance()->doComposition(
                        &fd_index_pair.first, 
                        nv_buffer_list, 
                        m_sourceFrameSize.m_width, 
                        m_sourceFrameSize.m_height);
                }

                if (fd_index_pair.first > 0)
                {
                    auto frame_data = std::make_shared<RawFrameParams>();
                    frame_data->m_fd = fd_index_pair.first;
                    frame_data->m_sourceWidth = m_sourceFrameSize.m_width;
                    frame_data->m_sourceHeight = m_sourceFrameSize.m_height;
                    frame_data->m_targetWidth = target_width;
                    frame_data->m_targetHeight = target_height;
                    frame_data->m_isTransformed = false;
                    frame_data->m_fdWrapperObj = new std::shared_ptr<fdWrapper>(
                        std::make_shared<fdWrapper>(m_surfacePool, frame_data->m_fd, -1));
                    
                    m_consumer->onFrame(frame_data);
                    frame_count++;
                }
            }

            // Update timing for next frame using proper chrono duration
            next_frame_time = next_frame_time + frame_interval;

            // Reset timing if we're falling behind
            if (current_time > next_frame_time + frame_interval)
            {
                LOG(warning) << "Compositor falling behind, resetting timing" << endl;
                next_frame_time = current_time + frame_interval;
            }

            // Log FPS every 5 seconds
            if (current_time - fps_check_time >= std::chrono::seconds(5))
            {
                LOG(info) << "Compositor FPS: " << frame_count / 5 << endl;
                frame_count = 0;
                fps_check_time = current_time;
            }
        }

        // Small sleep to prevent busy waiting
        if (next_frame_time > current_time)
        {
            std::this_thread::sleep_for(std::chrono::microseconds(100));
        }
    }

    // Cleanup
    for (auto& buffer : nv_buffer_list) {
        if (buffer.second && buffer.second->m_sample) {
            gst_sample_unref(buffer.second->m_sample);
        }
    }
    nv_buffer_list.clear();

    LOG(warning) << "Exiting from compose samples thread" << endl;
}

// Set custom grid layout
void NvCompositor::setCustomGridLayout(const GridLayout& layout)
{
    std::lock_guard<std::mutex> lock(m_gridLayoutLock);
    m_gridLayout = layout;
    LOG(info) << "Custom grid layout set: " << layout.rows << "x" << layout.columns 
              << " with " << layout.tiles.size() << " tiles" << endl;
}

// Set grid layout from JSON configuration
void NvCompositor::setGridLayoutFromJson(const std::string& jsonConfig)
{
    try {
        GridLayout layout = parseJsonLayout(jsonConfig);
        setCustomGridLayout(layout);
    } catch (const std::exception& e) {
        LOG(error) << "Failed to parse JSON layout: " << e.what() << endl;
    }
}

// Set grid layout from AI prompt
void NvCompositor::setGridLayoutFromPrompt(const std::string& aiPrompt)
{
    try {
        GridLayout layout = parseAiPromptLayout(aiPrompt);
        setCustomGridLayout(layout);
    } catch (const std::exception& e) {
        LOG(error) << "Failed to parse AI prompt layout: " << e.what() << endl;
    }
}

// Get current layout
GridLayout NvCompositor::getCurrentLayout() const
{
    std::lock_guard<std::mutex> lock(m_gridLayoutLock);
    return m_gridLayout;
}

// Parse JSON layout configuration using new schema
GridLayout NvCompositor::parseJsonLayout(const std::string& jsonConfig)
{
    Json::Value root;
    Json::Reader reader;
    
    if (!reader.parse(jsonConfig, root)) {
        throw std::invalid_argument("Invalid JSON format");
    }
    
    GridLayout layout;
    layout.isCustom = true;
    
    // Parse grid dimensions (required fields)
    if (!root.isMember("columns") || !root.isMember("rows")) {
        throw std::invalid_argument("Missing required fields: columns and rows");
    }
    
    layout.columns = root["columns"].asInt();
    layout.rows = root["rows"].asInt();
    
    if (layout.columns < 1 || layout.rows < 1) {
        throw std::invalid_argument("Columns and rows must be at least 1");
    }
    
    // Parse tile spacing (required field)
    if (!root.isMember("tileSpacing")) {
        throw std::invalid_argument("Missing required field: tileSpacing");
    }
    
    const Json::Value& spacing = root["tileSpacing"];
    if (!spacing.isMember("horizontal") || !spacing.isMember("vertical")) {
        throw std::invalid_argument("tileSpacing must have horizontal and vertical values");
    }
    
    layout.tileSpacing.horizontal = spacing["horizontal"].asInt();
    layout.tileSpacing.vertical = spacing["vertical"].asInt();
    
    // Parse tiles (required field)
    if (!root.isMember("tiles") || !root["tiles"].isArray()) {
        throw std::invalid_argument("Missing required field: tiles (must be array)");
    }
    
    const Json::Value& tiles = root["tiles"];
    for (const auto& tile : tiles) {
        // Validate required fields
        std::vector<std::string> requiredFields = {"id", "userId", "row", "column", "videoUrl"};
        for (const auto& field : requiredFields) {
            if (!tile.isMember(field)) {
                throw std::invalid_argument("Missing required tile field: " + field);
            }
        }
        
        GridTile gridTile;
        gridTile.id = tile["id"].asString();
        gridTile.userId = tile["userId"].asString();
        gridTile.row = tile["row"].asInt();
        gridTile.column = tile["column"].asInt();
        gridTile.videoUrl = tile["videoUrl"].asString();
        
        // Optional width and height (default to 1)
        gridTile.width = tile.get("width", 1).asInt();
        gridTile.height = tile.get("height", 1).asInt();
        
        // Validate tile position is within grid bounds
        if (gridTile.row < 0 || gridTile.row >= layout.rows ||
            gridTile.column < 0 || gridTile.column >= layout.columns) {
            throw std::invalid_argument("Tile position out of grid bounds: tile " + gridTile.id);
        }
        
        // Validate tile size doesn't exceed grid bounds
        if (gridTile.row + gridTile.height > layout.rows ||
            gridTile.column + gridTile.width > layout.columns) {
            throw std::invalid_argument("Tile size exceeds grid bounds: tile " + gridTile.id);
        }
        
        layout.tiles.push_back(gridTile);
    }
    
    // Calculate tile positions as percentages
    layout.calculateTilePositions();
    
    LOG(info) << "Parsed JSON layout: " << layout.columns << "x" << layout.rows 
              << " with " << layout.tiles.size() << " tiles" << endl;
    
    return layout;
}

// Calculate tile positions as percentages for rendering
void GridLayout::calculateTilePositions()
{
    if (columns == 0 || rows == 0) return;
    
    // Calculate cell dimensions as percentages
    int cellWidthPercent = 100 / columns;
    int cellHeightPercent = 100 / rows;
    
    for (auto& tile : tiles) {
        // Calculate position based on grid coordinates
        tile.leftPercent = tile.column * cellWidthPercent;
        tile.topPercent = tile.row * cellHeightPercent;
        
        // Calculate size based on tile width/height in grid units
        tile.widthPercent = tile.width * cellWidthPercent;
        tile.heightPercent = tile.height * cellHeightPercent;
        
        // Apply spacing adjustments (reduce size to account for spacing)
        // Note: Spacing is in pixels, but we're working in percentages here
        // The actual pixel spacing will be applied during composition
        if (tileSpacing.horizontal > 0 || tileSpacing.vertical > 0) {
            // Reduce tile size slightly to account for spacing
            // This is an approximation since we don't know the final pixel dimensions
            tile.widthPercent = std::max(1, tile.widthPercent - 1);
            tile.heightPercent = std::max(1, tile.heightPercent - 1);
        }
    }
}

// Parse AI prompt for layout configuration
GridLayout NvCompositor::parseAiPromptLayout(const std::string& prompt)
{
    GridLayout layout;
    layout.isCustom = true;
    
    // Convert to lowercase for easier parsing
    std::string lowerPrompt = prompt;
    std::transform(lowerPrompt.begin(), lowerPrompt.end(), lowerPrompt.begin(), ::tolower);
    
    // Extract grid dimensions using regex
    std::regex gridRegex(R"((\d+)\s*x\s*(\d+))");
    std::smatch match;
    
    if (std::regex_search(lowerPrompt, match, gridRegex)) {
        layout.columns = std::stoi(match[2].str()); // Note: swapped for columns x rows format
        layout.rows = std::stoi(match[1].str());
    } else {
        // Default to 2x2 if not specified
        layout.columns = 2;
        layout.rows = 2;
    }
    
    // Extract spacing
    std::regex spacingRegex(R"(spacing[:\s]*(\d+))");
    if (std::regex_search(lowerPrompt, match, spacingRegex)) {
        int spacing = std::stoi(match[1].str());
        layout.tileSpacing.horizontal = spacing;
        layout.tileSpacing.vertical = spacing;
    } else {
        layout.tileSpacing.horizontal = 1;
        layout.tileSpacing.vertical = 1;
    }
    
    // Parse special layout patterns
    if (lowerPrompt.find("main") != std::string::npos && lowerPrompt.find("pip") != std::string::npos) {
        // Picture-in-Picture layout
        layout.columns = 1;
        layout.rows = 1;
        
        // Main window (full screen)
        GridTile mainTile("main", "system", 0, 0, 1, 1, "");
        mainTile.topPercent = 0;
        mainTile.leftPercent = 0;
        mainTile.widthPercent = 100;
        mainTile.heightPercent = 100;
        layout.tiles.push_back(mainTile);
        
        // PiP window (small overlay)
        GridTile pipTile("pip", "system", 0, 0, 1, 1, "");
        pipTile.topPercent = 75;
        pipTile.leftPercent = 75;
        pipTile.widthPercent = 25;
        pipTile.heightPercent = 25;
        layout.tiles.push_back(pipTile);
        
    } else if (lowerPrompt.find("side by side") != std::string::npos) {
        // Side by side layout
        layout.columns = 2;
        layout.rows = 1;
        
        GridTile leftTile("left", "user1", 0, 0, 1, 1, "");
        GridTile rightTile("right", "user2", 0, 1, 1, 1, "");
        layout.tiles.push_back(leftTile);
        layout.tiles.push_back(rightTile);
        
    } else if (lowerPrompt.find("quad") != std::string::npos) {
        // Quad layout
        layout.columns = 2;
        layout.rows = 2;
        
        for (int r = 0; r < 2; r++) {
            for (int c = 0; c < 2; c++) {
                GridTile tile("tile_" + std::to_string(r * 2 + c), 
                            "user" + std::to_string(r * 2 + c), r, c, 1, 1, "");
                layout.tiles.push_back(tile);
            }
        }
    } else {
        // Default grid layout
        for (int r = 0; r < layout.rows; r++) {
            for (int c = 0; c < layout.columns; c++) {
                GridTile tile("tile_" + std::to_string(r * layout.columns + c), 
                            "user" + std::to_string(r * layout.columns + c), r, c, 1, 1, "");
                layout.tiles.push_back(tile);
            }
        }
    }
    
    // Calculate tile positions
    layout.calculateTilePositions();
    
    LOG(info) << "Parsed AI prompt layout: " << layout.columns << "x" << layout.rows 
              << " with " << layout.tiles.size() << " tiles" << endl;
    
    return layout;
}

// Calculate custom layout positions
void NvCompositor::calculateCustomLayout(NvBufSurfTransformRect* dstCompRect, size_t list_size, 
                                        uint32_t target_w, uint32_t target_h)
{
    std::lock_guard<std::mutex> lock(m_gridLayoutLock);
    
    if (!m_gridLayout.isCustom || m_gridLayout.tiles.empty()) {
        calculateDefaultLayout(dstCompRect, list_size, target_w, target_h);
        return;
    }
    
    // Use custom layout with new tile structure
    for (size_t i = 0; i < list_size && i < m_gridLayout.tiles.size(); i++) {
        const GridTile& tile = m_gridLayout.tiles[i];
        
        // Convert percentage to actual pixels
        dstCompRect[i].left = (tile.leftPercent * target_w) / 100;
        dstCompRect[i].top = (tile.topPercent * target_h) / 100;
        dstCompRect[i].width = (tile.widthPercent * target_w) / 100;
        dstCompRect[i].height = (tile.heightPercent * target_h) / 100;
        
        // Apply pixel spacing
        if (m_gridLayout.tileSpacing.horizontal > 0 || m_gridLayout.tileSpacing.vertical > 0) {
            // Adjust position to account for spacing
            dstCompRect[i].left += (tile.column * m_gridLayout.tileSpacing.horizontal);
            dstCompRect[i].top += (tile.row * m_gridLayout.tileSpacing.vertical);
            
            // Reduce size to account for spacing
            dstCompRect[i].width -= m_gridLayout.tileSpacing.horizontal;
            dstCompRect[i].height -= m_gridLayout.tileSpacing.vertical;
            
            // Ensure minimum size
            dstCompRect[i].width = std::max(dstCompRect[i].width, static_cast<uint32_t>(1));
            dstCompRect[i].height = std::max(dstCompRect[i].height, static_cast<uint32_t>(1));
        }
    }
    
    LOG(verbose) << "Using custom grid layout with " << m_gridLayout.tiles.size() << " tiles" << endl;
}

// Calculate default layout (existing logic)
void NvCompositor::calculateDefaultLayout(NvBufSurfTransformRect* dstCompRect, size_t list_size, 
                                         uint32_t target_w, uint32_t target_h)
{
    // This contains the existing layout logic that was in doComposition
    int32_t spacing = 1;
    int32_t scaling_factor = 1;
    int32_t top_offset = 0;
    
    if (list_size <= 4) {
        scaling_factor = 2;
    }
    if (list_size > 4) {
        scaling_factor = 3;
    }
    if (list_size > 9) {
        scaling_factor = 4;
    }
    
    if (scaling_factor <= 0)
    {
        LOG(warning) << "Invalid scaling_factor value (" << scaling_factor << ") in compositor layout, using default: " << DEFAULT_SCALING_FACTOR << endl;
        scaling_factor = DEFAULT_SCALING_FACTOR;
    }
    
    int32_t cellWidth = (target_w / scaling_factor) - spacing;
    int32_t cellHeight = (target_h / scaling_factor) - spacing;

    // Set common cell width and height for all
    for (size_t i = 0; i < list_size; i++) {
        dstCompRect[i].width = cellWidth;
        dstCompRect[i].height = cellHeight;
    }
    
    // Apply existing layout logic based on stream count
    if (list_size == 1) {
        dstCompRect[0].top = 0;
        dstCompRect[0].left = 0;
        dstCompRect[0].width = target_w;
        dstCompRect[0].height = target_h;
    } else if (list_size <= 2) {
        dstCompRect[0].top = cellHeight / 2;
        dstCompRect[0].left = 0;
        dstCompRect[1].top = cellHeight / 2;
        dstCompRect[1].left = cellWidth;
    } else if (list_size > 2 && list_size <= 4) {
        dstCompRect[0].top = 0;
        dstCompRect[0].left = 0;
        dstCompRect[1].top = 0;
        dstCompRect[1].left = cellWidth;
        dstCompRect[2].top = cellHeight;
        dstCompRect[2].left = cellWidth / 2;
        if (list_size > 3) {
            dstCompRect[2].left = 0;
            dstCompRect[3].top = cellHeight + top_offset;
            dstCompRect[3].left = cellWidth;
        }
    } else {
        // For more complex layouts, use a simple grid approach
        int cols = (list_size <= 9) ? 3 : 4;
        if (cols <= 0)
        {
            LOG(warning) << "Invalid cols value (" << cols << ") in compositor layout, using default: " << DEFAULT_GRID_COLS << endl;
            cols = DEFAULT_GRID_COLS;
        }
        
        int rows = (list_size + cols - 1) / cols; // Ceiling division
        if (rows <= 0)
        {
            LOG(warning) << "Invalid rows value (" << rows << ") in compositor layout, using default: " << DEFAULT_GRID_ROWS << endl;
            rows = DEFAULT_GRID_ROWS;
        }
        
        int gridCellWidth = target_w / cols;
        int gridCellHeight = target_h / rows;
        
        for (size_t i = 0; i < list_size; i++) {
            int row = i / cols;
            int col = i % cols;
            
            dstCompRect[i].left = col * gridCellWidth;
            dstCompRect[i].top = row * gridCellHeight;
            dstCompRect[i].width = gridCellWidth - spacing;
            dstCompRect[i].height = gridCellHeight - spacing;
        }
    }
}
