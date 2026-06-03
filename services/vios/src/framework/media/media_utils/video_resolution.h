/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

inline constexpr int WIDTH_2160p  = 3840;
inline constexpr int HEIGHT_2160p = 2160;

inline constexpr int HEIGHT_1440p = 1440;

inline constexpr int WIDTH_1080p  = 1920;
inline constexpr int HEIGHT_1080p = 1080;

inline constexpr int WIDTH_720p   = 1280;
inline constexpr int HEIGHT_720p  = 720;

/*
 * Changing width from 854 as it is not multiple of 8
 * Bug 4561987
 */
inline constexpr int WIDTH_480p   = 856;
inline constexpr int HEIGHT_480p  = 480;

inline constexpr int WIDTH_360p   = 640;
inline constexpr int HEIGHT_360p  = 360;

inline constexpr int WIDTH_240p   = 426;
inline constexpr int HEIGHT_240p  = 240;

inline constexpr int WIDTH_144p   = 256;
inline constexpr int HEIGHT_144p  = 144;

inline constexpr int VIDEO_FRAME_RES_2160p = WIDTH_2160p * HEIGHT_2160p;
inline constexpr int VIDEO_FRAME_RES_1080p = WIDTH_1080p * HEIGHT_1080p;
inline constexpr int VIDEO_FRAME_RES_720p  = WIDTH_720p  * HEIGHT_720p;
inline constexpr int VIDEO_FRAME_RES_480p  = WIDTH_480p  * HEIGHT_480p;
inline constexpr int VIDEO_FRAME_RES_360p  = WIDTH_360p  * HEIGHT_360p;
