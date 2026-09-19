// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import {API_ENDPOINT} from "../common/axios_instance";

export function getMediaUrl(mediaPath){
    return mediaPath ? API_ENDPOINT.split("/api")[0] + mediaPath : "";
}
