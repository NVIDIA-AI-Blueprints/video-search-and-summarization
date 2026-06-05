# VIOS UI Selectors — Verified Playwright Patterns

Reference guide for the vios-dev-sanity agent. These patterns are confirmed working against the VIOS React/MUI frontend. **Read this file at the start of each test session.**

---

## MUI Autocomplete — Sensor Dropdowns

All sensor/tag dropdowns are MUI Autocomplete components. Used on live-streams, recorded-streams, video-wall, sensor-details, sensor-management pages.

```javascript
// Open the dropdown — use JS dispatch, not browser_click on label
const visibleAcs = Array.from(document.querySelectorAll('.MuiAutocomplete-root'))
  .filter(ac => ac.offsetParent !== null);

// Index guide:
//   live-streams / recorded-streams: [0] = Select Tags, [1] = Select Sensors
//   video-wall / sensor-details / sensor-management: [0] = Select Sensors
const sensorAc = visibleAcs[N];
const input = sensorAc.querySelector('input');
input.focus();
input.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
input.dispatchEvent(new MouseEvent('click', {bubbles: true}));

// Click an option by name
const opt = Array.from(document.querySelectorAll('[role="option"]'))
  .find(o => o.textContent.trim() === 'sensor_name');
if (opt) opt.click();
```

**Critical:** The dropdown closes after every option click. To select multiple sensors, reopen (re-dispatch mousedown/click) between each selection.

**Why JS dispatch and not browser_click:** MUI Autocomplete requires `mousedown` + `click` to open. `get_by_label` resolves to the wrong element because the SPA renders all page panels into the DOM simultaneously — only visible panels have `offsetParent !== null`.

---

## React Input Fields — Sensor Management Form

Direct `.value = x` does not trigger React state. Use the native value setter:

```javascript
const input = document.querySelector('input[placeholder="..."]'); // or find by label
const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
setter.call(input, 'new value');
input.dispatchEvent(new Event('input', {bubbles: true}));
input.dispatchEvent(new Event('change', {bubbles: true}));
```

**Why:** React shadows the `.value` property. The native setter triggers React's synthetic event, enabling submit buttons that were previously disabled.

---

## Video Wall — Compositor Architecture

The Video Wall produces **one merged WebRTC stream** regardless of how many sensors are selected. Always expect exactly 1 active `<video>` element with `videoWidth > 0`. The sensor count is in the body text: `"Streaming N sensors"`.

- Requires >= 2 sensors selected before "Start Video Wall" enables
- Do not count DOM video elements to infer sensor count

---

## RTSP Sensor Add — Deduplication

`POST /vst/api/v1/sensor/add` deduplicates by upstream stream identity (stream metadata), not by the `name` field. Adding a URL that resolves to an already-known stream silently returns the existing sensor. To add a genuinely new sensor, use an RTSP URL whose stream is not already registered, or upload a new file to NvStreamer and scan.

---

## Confirmed API Endpoints

All relative to `BASE_URL`:

| Method | Path | Purpose |
|---|---|---|
| GET | `/vst/api/v1/sensor/list` | All sensors with details |
| GET | `/vst/api/v1/sensor/streams` | Stream URLs |
| GET | `/vst/api/v1/sensor/status` | Health check (200 = up) |
| POST | `/vst/api/v1/sensor/add` | Add sensor `{name, sensorUrl, username, password}` |
| DELETE | `/vst/api/v1/sensor/<id>` | Remove sensor (returns `true`) |
| POST | `/vst/api/v1/sensor/scan` | Trigger NvStreamer scan |

---

## Non-Blocking Console Errors

Do not treat these as failures — they appear in every healthy session:

| Message | Reason |
|---|---|
| `streambridge 404` | Not deployed in single-process stack |
| `Calibration fetch :8081 ERR_CONNECTION_REFUSED` | Spatial AI not deployed |
| `ICE candidate error` WARN | No STUN/TURN; streams use direct connection |
| `WebRTC Issue detected: {type: cpu}` | Quality warning, not a failure |
