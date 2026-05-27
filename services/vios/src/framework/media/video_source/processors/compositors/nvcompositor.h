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

#pragma once

#include <iostream>
#include <queue>
#include <mutex>
#include <string>
#include <vector>
#include <condition_variable>
#include <regex>
#include <jsoncpp/json/json.h>
#include "media_consumer.h"
#include "nvvideoencoder.h"

using namespace std;

// Grid layout data structures matching JSON schema
struct TileSpacing {
    int horizontal = 1;
    int vertical = 1;
    
    TileSpacing() = default;
    TileSpacing(int h, int v) : horizontal(h), vertical(v) {}
};

struct GridTile {
    std::string id = "";
    std::string userId = "";
    int row = 0;
    int column = 0;
    int width = 1;           // Width in grid units (not percentage)
    int height = 1;          // Height in grid units (not percentage)
    std::string videoUrl = "";
    
    // Calculated position values (percentages for rendering)
    int topPercent = 0;
    int leftPercent = 0;
    int widthPercent = 0;
    int heightPercent = 0;
    
    GridTile() = default;
    GridTile(const std::string& tileId, const std::string& user, int r, int c, 
             int w, int h, const std::string& url)
        : id(tileId), userId(user), row(r), column(c), width(w), height(h), videoUrl(url) {}
};

struct GridLayout {
    int columns = 0;
    int rows = 0;
    TileSpacing tileSpacing;
    std::vector<GridTile> tiles;
    bool isCustom = false;
    
    GridLayout() = default;
    GridLayout(int cols, int r, const TileSpacing& spacing = TileSpacing()) 
        : columns(cols), rows(r), tileSpacing(spacing) {}
    
    // Helper method to calculate tile positions
    void calculateTilePositions();
};

class NvCompositor : public IMediaDataConsumer
{
public:
    NvCompositor(const GridLayout& gridLayout); // Constructor that extracts URLs from GridLayout
    ~NvCompositor();

    void doCompositeTask();
    void setTargetFrameSize (FrameSize& target_size);
    void onFrame(std::shared_ptr<RawFrameParams> frame_data) override;
    void setConsumer(std::shared_ptr<IMediaDataConsumer> consumer);
    void setOriginalFrameSize() override;
    void setFrameRate(string frame_rate);
    
    // New methods for grid layout management
    void setCustomGridLayout(const GridLayout& layout);
    void setGridLayoutFromJson(const std::string& jsonConfig);
    void setGridLayoutFromPrompt(const std::string& aiPrompt);
    GridLayout getCurrentLayout() const;
    static GridLayout parseJsonLayout(const std::string& jsonConfig);
private:
    std::vector<string>                            m_urlsList;
    std::shared_ptr<IMediaDataConsumer>            m_consumer    = nullptr;
    std::thread                                    m_compositorThread;
    shared_ptr<NvSurfacePool>                      m_surfacePool = nullptr;
    std::vector<string>                            m_streamIDList;
    std::atomic<bool>                              m_stop {false};
    FrameSize                                      m_targetFrameSize{1920, 1080};
    FrameSize                                      m_sourceFrameSize{1920, 1080};
    std::mutex                                     m_targetFrameSizeLock;

    /* Data structure related to Queue */
    std::queue<std::shared_ptr<RawFrameParams>> m_queue;
    std::mutex                                     m_queueLock;
    std::condition_variable                        m_condVar;
    atomic<bool>                                   m_flowData {false};
    double                                         m_frameRate = 30.0;
    
    // New grid layout members
    GridLayout                                     m_gridLayout;
    mutable std::mutex                             m_gridLayoutLock;
    
    void updateGstMap(std::shared_ptr<RawFrameParams> frame_data);
    void pushEmptyFrame ();
    
    // New private methods for layout calculation
    void calculateCustomLayout(NvBufSurfTransformRect* dstCompRect, size_t list_size, 
                              uint32_t target_w, uint32_t target_h);
    void calculateDefaultLayout(NvBufSurfTransformRect* dstCompRect, size_t list_size, 
                               uint32_t target_w, uint32_t target_h);
    GridLayout parseAiPromptLayout(const std::string& prompt);

};