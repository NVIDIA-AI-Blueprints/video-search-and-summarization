// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0


export function isFirstVertexMinimum(polygonVertices) {
    if (polygonVertices.length < 3) {
      return false;
    }

    const firstVertex = polygonVertices[0];
    let minX = firstVertex.lng;
    let minY = firstVertex.lat;

    for (let i = 1; i < polygonVertices.length; i++) {
      const currentVertex = polygonVertices[i];

      if (currentVertex.lng < minX) {
        minX = currentVertex.lng;
        minY = currentVertex.lat;
      } else if (currentVertex.lng === minX && currentVertex.lat < minY) {
        minY = currentVertex.lat;
      }
    }

    return firstVertex.lng === minX && firstVertex.lat === minY;
  }

//   // Example usage
//   const polygon = [
//     { x: 2, y: 3 },
//     { x: 5, y: 7 },
//     { x: 1, y: 9 },
//     // ... other vertices
//   ];

//   const isFirstMin = isFirstVertexMinimum(polygon);