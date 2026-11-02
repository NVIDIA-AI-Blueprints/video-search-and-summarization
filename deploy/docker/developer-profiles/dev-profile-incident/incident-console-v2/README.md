# Incident Console v2

The Next.js web console for the incident profile: upload a clip, run the VLM analysis, review and edit the report as versions, mark one official, search incidents and evidence in `/library`, and ask questions about a report.

## Run it

From `deploy/docker/developer-profiles/dev-profile-incident/`:

```bash
./start.sh --mode mock   # mock backend and VLM gateway on this laptop
./start.sh --mode vm     # the agent on kwanz-ws over an SSH tunnel
```

In vm mode the console reaches only the agent (`localhost:8000`, the one tunnelled port) and uploads videos straight to R2 through its own `/api/uploads/r2` route; it uses no VST and no `:7777` proxy, which are removed from the VM deployment along with HAProxy and Redis (Phoenix stays for eval traces, and the person-tracking stack runs by default).

Either way the console opens on `http://127.0.0.1:3200`; `GET /api/health` should report `ready`. To run it by hand, start the [mock backend](../mock-backend/base_profile_mock/README.md) and the [VLM gateway](../vlm-gateway/README.md), then `npm install && npm run dev` here. Uploads need `ffmpeg` and `ffprobe` (every upload is probed and, unless it is already an H.264 MP4, converted to one at its original resolution before it goes to R2); evidence frames, crops and clips need `ffmpeg`. `npm install` fetches both binaries (`ffmpeg-static` and `@ffprobe-installer/ffprobe`), so no manual install is needed. The console uses `FFMPEG_PATH` or `FFPROBE_PATH` when set, then the bundled copy, then the one on your PATH; see [`src/lib/video/host-binaries.ts`](src/lib/video/host-binaries.ts).

The report header's **Blur faces** switch stores a per-video setting (`videos.blur_faces`) and re-analyses the video as a background job with the agent pixelating every head it finds in the stored evidence frames (a whole-frame head detection, whatever tracking linked); evidence clips of such a video are cut blurred from the heads the agent found in each clip's window ([`../.docs/data.md`](../.docs/data.md#privacy-blur)).

Settings come from `.env.local`, a symlink to the profile's shared `../.env.local`; edit that file, not the symlink.

Analyses run as background jobs: the page uploads and converts each file, then queues its analysis in the `analysis_jobs` table, and a loop inside this console's server process (started by `src/instrumentation.ts`) analyses the queued jobs one at a time. Closing the tab after a file shows Queued loses nothing, and the upload page lists running and queued jobs (a finished job leaves the list). Every console pointed at finals with the loop on takes jobs from the same queue; set `ANALYSIS_JOB_LOOP=off` to keep this one out. Details: [`../.docs/architecture.md`](../.docs/architecture.md#background-analysis-jobs).

## Check it

```bash
npm run lint        # Biome lint and format check, then tsc --noEmit
npm run typecheck
npm test            # tests/*.test.mjs and src/**/*.test.mjs, including the import-boundary check
npm run format      # apply the Biome formatter
```

Tests need a Node.js with TypeScript type stripping (official Node 22+ builds); where it is missing (`ERR_NO_TYPESCRIPT`), run them with `npx -y node@22`.

## shadcn/ui

The console's screens are built on [shadcn/ui](https://ui.shadcn.com) (Radix, `radix-vega` preset) on Tailwind 4 and React 19, with the primitives in `src/components/ui/`. Some feature components stay custom (the video player and overlays, the versions graph, the evidence cards and the frame picker); add new UI from shadcn first.

Add a component from this directory (the CLI reads `components.json` here; from elsewhere pass `-c <path to this directory>`):

```bash
npx shadcn@latest add <component>     # e.g. popover; writes src/components/ui/<component>.tsx
npx shadcn@latest docs <component>    # usage and examples
```

- **Theme tokens:** CSS variables in `src/app/styles.css`. Light only, in `:root`; there is no dark theme. `@theme inline` maps them to Tailwind colours (`bg-primary`, `text-muted-foreground`, ...). The console's own colours (`ink`, `canvas`, `signal`, `moss`, `clay`), fonts and `shadow-panel` are in the `@theme` block of the same file; there is no `tailwind.config.js`.
- **Radius and font:** the shadcn radius override is left out, so `rounded-*` keeps Tailwind's stock scale, and the font stays Arial. Changing either restyles every existing screen.
- **Provider and toaster:** `Tooltip` needs `TooltipProvider` and `sonner` needs `<Toaster />` mounted in `src/app/layout.tsx`. `layout.tsx` mounts neither, so mount them before using either.
- **Agent tooling:** the shadcn skill is in `deploy/docker/developer-profiles/dev-profile-incident/skills/shadcn` (with symlinks at `.claude/skills/shadcn` and `.agents/skills/shadcn`, pinned in `skills-lock.json`) and the shadcn MCP server is in `.mcp.json` at the repo root. Restart Claude Code and run `/mcp` to see it connect.
- **Lint:** `src/components/ui/` is formatted to the repo's Biome style; keep that when adding components (`npm run format`).

## Learn more

- Pages, configuration, routes and report versions: [`../.docs/architecture.md`](../.docs/architecture.md#4-runtime-interaction-sequences)
- Code layout and import rules: [`../.docs/mvp2/console-hexagonal-architecture.md`](../.docs/mvp2/console-hexagonal-architecture.md)
- Analysis contract, validation and stored attempts: [`../.docs/analysis-schema.md`](../.docs/analysis-schema.md)
- Tables and R2 keys: [`../.docs/data.md`](../.docs/data.md)
