<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# @nv-metropolis-bp-vss-ui/map

Native warehouse Map tab: floor-plan analytics, camera replay, and Google Maps views.

Ported SD / mdat widgets live in `lib-src/common/warehouse` (imported as `common/warehouse`). They are not part of the shared `common` package.

## Usage

```tsx
import { MapComponent } from '@nv-metropolis-bp-vss-ui/map';

function App() {
  return (
    <MapComponent
      theme="light"
      mapData={{
        apiUrl: process.env.NEXT_PUBLIC_WAREHOUSE_ANALYTICS_API_URL,
        mediaStreamingUrl: process.env.NEXT_PUBLIC_WAREHOUSE_MEDIA_STREAMING_URL,
        googleMapsApiKey: process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY,
      }}
    />
  );
}
```

## Environment Variables

- `NEXT_PUBLIC_ENABLE_MAP_TAB` — show the Map tab
- `NEXT_PUBLIC_WAREHOUSE_ANALYTICS_API_URL` — video analytics API origin
- `NEXT_PUBLIC_WAREHOUSE_MEDIA_STREAMING_URL` — VST streaming API origin
- `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` — Maps JavaScript API key
- `NEXT_PUBLIC_WAREHOUSE_UI_CONFIG_URL` — optional override for the UI config JSON (default `/warehouse-analytics/default-ui-config.json`)
