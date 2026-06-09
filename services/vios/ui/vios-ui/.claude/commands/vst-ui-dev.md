---
name: vst-ui-dev
description: This skill should be used when the user asks to "add a feature", "fix a bug", "create a component", "implement UI", "update the VST UI", "change the dashboard", "modify the video player", or any other development task on the VST web client (vst-ui-ts TypeScript/React codebase). Covers the full development loop: plan → implement → lint/format → dev server → user review → commit → branch → MR.
argument-hint: <feature description>
allowed-tools: AskUserQuestion, Read, Edit, Write, Bash, Bash(npm run format), Bash(npm run lint), Bash(npm run install:link), Bash(git *)
---

## Overview

The VST web client (`vst-ui-ts/`) is a React + TypeScript + Vite + MUI application. It connects to three possible backend types — **VST**, **MMS**, and **NVStreamer** — detected at runtime. WebRTC streaming comes from an external library (`vst-streaming-lib`) installed via the `vst-web-streamer` repo when running `npm run install:link`.

Use this skill for any UI feature work. Follow the workflow top to bottom; skip steps only when clearly not applicable.

---

## Codebase Map

### Directory Layout

```
src/
├── assets/                    Static images and icons
├── components/                Shared/reusable components
│   ├── analyticsComponents/   Calibration data visualization
│   ├── overlaySettings/       Video overlay config panels (8 sub-components)
│   ├── sensorRecording/       Recording schedule and status cards
│   ├── sensorSelector/        Sensor picker dropdowns
│   ├── videoPlayer/           Main video player + WebRTC controls
│   │   └── videoPlayerUtils/  QoS widgets, bitrate sparklines, trick modes, analytics overlay
│   └── widget/                Dashboard widget wrapper
├── features/                  Feature-scoped components
│   ├── dashboardTables/       Sensor info, timeline gap, RTSP tables
│   ├── dashboardWidgets/      Metric summary widgets
│   ├── microserviceSettings/  Per-sensor microservice config
│   ├── rangePickerDialog/     Date/time range picker
│   ├── sensorManagement/      Add/remove/replace sensor operations
│   ├── streamManager/         Stream source and bitrate selection
│   ├── timeSlider/            Timeline navigation with markers
│   └── wrappers/              AdaptorWrapper (VST/MMS/Streamer detection)
├── hooks/                     Custom React hooks
├── interfaces/interfaces.ts   All TypeScript types (Sensor, Stream, Config, …)
├── layout/
│   ├── Layout.tsx             App shell: sidebar drawer, appbar
│   ├── nav/ListItems.tsx      Nav menu (adaptor-aware)
│   └── routes/Routes.tsx      React Router — two route trees (VST vs Streamer)
├── pages/
│   ├── common/                Shared pages + experimental feature hub
│   ├── nvstreamer/            NVStreamer pages (media upload, config, streams)
│   └── vst/                   VST pages + calibration multi-step workflow
├── services/
│   ├── Axios.tsx              HTTP client with interceptors
│   └── StateManagement.tsx    Zustand global store
├── theme/                     MUI theme, settings context, NVIDIA branding
├── utils/
│   ├── misc/                  Data sync, sensor helpers, logger, version utils
│   └── maths/translation.ts   Coordinate transforms (calibration)
├── config.tsx                 Backend endpoint URLs
└── App.tsx                    Root component
```

### Feature → File Mapping

| Feature | Primary files |
|---------|--------------|
| Sensor management | `pages/vst/SensorManagement.tsx`, `features/sensorManagement/`, `components/sensorSelector/` |
| Sensor configuration | `pages/vst/SensorConfiguration.tsx`, `components/sensorSettingsForm/`, `components/sensorInformationForm/` |
| Sensor recording | `pages/vst/SensorRecording.tsx`, `components/sensorRecording/` |
| Live streaming | `pages/vst/LiveStream.tsx`, `features/streamManager/`, `components/videoPlayer/VideoPlayer.tsx` |
| Replay / recordings | `pages/vst/ReplayStream.tsx`, `features/rangePickerDialog/`, `features/timeSlider/` |
| Video player & controls | `components/videoPlayer/VideoPlayer.tsx`, `components/videoPlayer/videoPlayerUtils/` |
| WebRTC / streaming lib | `features/streamManager/StreamManager.tsx`, `components/videoPlayer/VideoPlayer.tsx` — imports from `vst-streaming-lib` |
| QoS charts & bitrate | `components/videoPlayer/videoPlayerUtils/NetworkQualityWidget.tsx`, `videoPlayerUtils/BitrateSparkline.tsx`, `hooks/useBitrate.ts` |
| Video overlay settings | `components/overlaySettings/OverlaySettingsPanel.tsx` + sibling files |
| Analytics overlay | `components/analyticsComponents/`, `components/videoPlayer/videoPlayerUtils/analytics/` |
| Calibration workflow | `pages/vst/calibration-steps/Calibration.tsx` (orchestrator) + all sibling substep files |
| Video wall | `pages/vst/VideoWall.tsx`, `videoPlayerUtils/VideoWallPlaybackHandler.tsx` |
| VST dashboard | `pages/vst/VSTDashboard.tsx`, `features/dashboardWidgets/`, `features/dashboardTables/` |
| NVStreamer pages | `pages/nvstreamer/` (MediaUpload, MediaManagement, MediaStreams, MediaConfiguration) |
| Media upload | `pages/nvstreamer/MediaUpload.tsx`, `pages/common/experimentalPages/PutFileUpload.tsx` |
| Settings / preferences | `pages/common/experimentalPages/Settings.tsx`, `theme/settingsProvider.tsx`, `theme/defaultSettings.ts` |
| Microservice settings | `features/microserviceSettings/` |
| Global state | `services/StateManagement.tsx` (Zustand — sensors, streams, storage, service flags) |
| API calls | `services/Axios.tsx` + inline fetch calls in page/feature components |
| Data synchronization | `utils/misc/updateSensorsAndStreams.ts` (central polling/refresh) |
| Types | `interfaces/interfaces.ts` |
| Theme & styling | `theme/themeContextProvider.tsx`, `theme/themeContext.tsx` |
| Routing | `layout/routes/Routes.tsx` (DEFAULT_ROUTES for VST, STREAMER_ROUTES for NVStreamer) |
| Navigation | `layout/nav/ListItems.tsx`, `layout/Layout.tsx` |
| Adaptor detection | `features/wrappers/AdaptorWrapper.tsx` — queries version endpoint on startup |
| Experimental features | `pages/common/Experimental.tsx` + `pages/common/experimentalPages/` |
| Error handling | Per-endpoint flags in Zustand store; `components/errorBoundary/` |
| Logging | `utils/misc/Logger.ts`, `config.tsx` (`enableLogs` flag) |

### Key Architectural Facts

- **Three adaptor types:** At startup `AdaptorWrapper.tsx` detects whether the backend is `vst`, `mms`, or `streamer`. This switches the route tree and controls which features are visible.
- **WebRTC library:** `vst-streaming-lib` is not an npm registry package. It comes from the `vst-web-streamer` repo (`ssh://git@<INTERNAL_GITLAB_SSH>/L4TMM/vst-web-streamer.git`) and is sym-linked locally by `npm run install:link`. Never import it by relative path — always use `import { StreamManager } from 'vst-streaming-lib'`.
- **Backend endpoints:** All configured in `src/config.tsx`. In development they point to a live backend; the `/update-vst-ui` skill handles production deployment.
- **State:** Zustand store in `services/StateManagement.tsx`. Prefer reading state via store selectors; avoid prop-drilling through more than two levels.
- **Styling:** MUI v5 with `sx` prop or `styled`. Theme lives in `theme/themeContextProvider.tsx`. Dark/light toggle is context-driven. Use theme tokens, not hard-coded colours.
- **Scripts available:**
  - `npm run install:link` — installs deps and links vst-web-streamer
  - `npm run dev` — starts the Vite dev server
  - `npm run build` — production build → `dist/`
  - `npm run lint` — ESLint check
  - `npm run format` — Prettier format

---

## Development Workflow

### Step 1 — Understand the request

Read `$ARGUMENTS`. If the feature description is ambiguous, use `AskUserQuestion` to clarify scope before touching any code.

Identify the affected files using the feature map above. Read the relevant files before making changes.

### Step 2 — Plan

Briefly state (in text output, not a file):
- Which files will be modified or created
- What the component/logic boundary is
- Any state changes needed in `StateManagement.tsx`
- Any new API calls needed

If the plan is non-trivial, confirm with the user before proceeding.

### Step 3 — Implement

Make focused, minimal changes. Follow these conventions:

- **TypeScript:** Add types to `interfaces/interfaces.ts` if the type is shared; colocate local types otherwise.
- **State:** Add state to the Zustand store only if multiple components need it; use local `useState` for single-component state.
- **Components:** Place reusable components in `components/`; feature-scoped components in `features/`; page-level components in `pages/`.
- **API calls:** Use the `Axios` instance from `services/Axios.tsx`, not bare `fetch`.
- **Styling:** Use MUI `sx` prop or `styled`. Match the existing dark/light theme pattern. No inline hex colours.
- **Comments:** Only when the *why* is non-obvious. No explanatory summaries.

### Step 4 — Lint and format

Run both checks after implementation is complete:

```bash
npm run format
npm run lint
```

Fix every lint error before proceeding. Do not skip or suppress rules without user approval.

### Step 5 — Start the dev server

Invoke the `/ui-dev-server` skill to configure the backend endpoint and start the Vite dev server.

Then use `AskUserQuestion` to tell the user:

> "Dev server is running. Please check the feature in your browser and let me know if anything needs changing."

### Step 6 — Iterate on feedback

Repeat Steps 3–5 for each round of user feedback until the user confirms the feature is complete.

When the user is satisfied, proceed.

### Step 7 — Final lint and format pass

```bash
npm run format
npm run lint
```

Ensure the working tree is clean before committing.

### Step 8 — Commit on a new branch

Check the current branch:

```bash
git branch --show-current
git log --oneline -5
```

Create a feature branch from `main` (use a short, descriptive kebab-case name):

```bash
git checkout -b <feature-name>
```

Stage only the files you changed — do not use `git add .` or `git add -A`:

```bash
git add <file1> <file2> ...
git status
```

Write a concise commit message (imperative mood, ≤72 chars on the first line):

```bash
git commit -m "$(cat <<'EOF'
<Short summary of what and why>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

### Step 9 — Report and prompt for MR

Tell the user:
- Branch name created
- Commit SHA and message
- Files changed
- That the commit has **not** been pushed yet

Then say:

> "To open a merge request, push the branch and create an MR on GitLab:
> ```
> git push -u origin <branch-name>
> ```
> Then visit the GitLab project to open the MR, or I can do it for you if you have the MaaS GitLab MCP server configured."

---

## Related Skills

- `/ui-dev-server` — Configure backend IP and start the Vite dev server
- `/update-vst-ui` — Build and deploy static assets to the vms_shim repo (VIOS/VST deployment)
- `/security-review` — Run before committing sensitive changes
