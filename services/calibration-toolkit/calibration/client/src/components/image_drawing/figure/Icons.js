// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import L from 'leaflet';

const iconCamera = new L.Icon({
    iconUrl: require('../../../assets/camera-no-orientation.png'),
    iconRetinaUrl: require('../../../assets/camera-no-orientation.png'),
    iconAnchor: null,
    popupAnchor: null,
    shadowUrl: require('../../../assets/marker-shadow.png'),
    shadowSize: 1,
    shadowAnchor: [4, 62],
    iconSize: new L.Point(25, 25),
    className: 'leaflet-camera-marker-icon'
});

export { iconCamera };