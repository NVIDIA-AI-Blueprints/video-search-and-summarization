<!-- SPDX-License-Identifier: MIT -->
# @nv-metropolis-bp-vss-ui/map

Map component for embedding external map applications (e.g., from port 3002).

## Usage

```tsx
import { MapComponent } from '@nv-metropolis-bp-vss-ui/map';

function App() {
  return (
    <MapComponent 
      theme="dark"
      mapData={{
        mapUrl: 'http://localhost:3002'
      }}
    />
  );
}
```

## Environment Variables

- `NEXT_PUBLIC_MAP_URL` - URL of the map application (default: http://localhost:3002)

