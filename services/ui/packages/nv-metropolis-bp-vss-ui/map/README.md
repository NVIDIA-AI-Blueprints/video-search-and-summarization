<!--
SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# @nv-metropolis-bp-vss-ui/map

Native Map tab with two sibling apps under `lib-src/`:

- `warehouse` — floor-plan analytics, camera replay, and Google Maps views
- `traffic-light` — smart-cities ITS (intersections, road network, alerts)

`MapComponent` selects the app from `NEXT_PUBLIC_MAP_APP` (`warehouse` by default; `traffic-light` when smartcities is deployed).

Shared widgets live in `lib-src/common` and are imported as `common/map`. That alias is the Map kit. It is not the shared `packages/common` package, which stays `common`.

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
- `NEXT_PUBLIC_MAP_APP` — `warehouse` (default) or `traffic-light`
- `NEXT_PUBLIC_WAREHOUSE_ANALYTICS_API_URL` — video analytics API origin
- `NEXT_PUBLIC_WAREHOUSE_MEDIA_STREAMING_URL` — VST streaming API origin
- `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY` — Maps JavaScript API key
- `NEXT_PUBLIC_WAREHOUSE_UI_CONFIG_URL` — optional override for the warehouse UI config JSON (default `/warehouse-analytics/default-ui-config.json`)
- `NEXT_PUBLIC_TRAFFIC_LIGHT_UI_CONFIG_URL` — optional override for the traffic-light UI config JSON (default `/traffic-light/default-ui-config.json`)
