---
description: Build the VST UI and deploy the static files into the vios tree (services/vios of the video-search-and-summarization repo), both ingress/vst-ui and webroot, then commit.
argument-hint: [/path/to/services/vios]
allowed-tools: AskUserQuestion, Read, Bash(cd * && npm run install:link), Bash(cd * && npm run build), Bash(ls *), Bash(rm -rf *), Bash(cp -r *), Bash(git -C * log *), Bash(git -C * add *), Bash(git -C * status), Bash(git -C * commit *)
---

## Task

Build the VST UI and deploy the compiled static assets into the `vios` component (`services/vios`) of the video-search-and-summarization repository, replacing the old files in both deployment locations, then commit the repo.

The UI source lives at `<VIOS_DIR>/ui/vios-ui/` (package `vst-ui-ts`). The WebRTC streaming library is the sibling `<VIOS_DIR>/ui/streaming-lib/` directory, built and linked by `npm run install:link`.

**Arguments provided:** $ARGUMENTS

---

## Step 1 — Locate the vios tree

The VST UI source and its deployment targets all live inside the `vios` component (`services/vios/`) of the `video-search-and-summarization` monorepo. This skill runs from a session opened in that monorepo, so the tree is already checked out — no cloning is needed. Resolve `VIOS_DIR` (the path to the `services/vios` directory):

1. **Argument provided** — if `$ARGUMENTS` is non-empty, use that path directly (it should point at the `services/vios` directory).
2. **Current directory is the vios tree** — if it contains `webroot` and `deployment/scaling/ingress`, use `.`.
3. **Monorepo root** — if `./services/vios` exists, use it.

Verify the resolved directory contains `ui/vios-ui` and `deployment/scaling/ingress`. If none of the above resolves (you are not inside a checkout), ask the user for the path to their `services/vios` directory rather than cloning.

Store the resolved path as `VIOS_DIR` for subsequent steps.

The two deployment targets inside `VIOS_DIR` are:
- `TARGET_INGRESS = $VIOS_DIR/deployment/scaling/ingress/vst-ui`
- `TARGET_WEBROOT = $VIOS_DIR/webroot`

The UI source directory is `UI_DIR = $VIOS_DIR/ui/vios-ui`.

---

## Step 2 — Remove old VST UI assets from both targets

Remove only the VST UI files; do **not** touch anything else in `webroot`.

```bash
rm -rf $TARGET_INGRESS/assets $TARGET_INGRESS/favicon $TARGET_INGRESS/index.html
rm -rf $TARGET_WEBROOT/assets $TARGET_WEBROOT/favicon $TARGET_WEBROOT/index.html
```

---

## Step 3 — Install dependencies in the VST UI repo

Run from the UI source directory (`$UI_DIR`, the directory containing `package.json`):

```bash
cd $UI_DIR && npm run install:link
```

This builds the sibling `ui/streaming-lib` package and links it as `vst-streaming-lib`. Wait for it to complete before continuing.

---

## Step 4 — Build the VST UI static files

```bash
cd $UI_DIR && npm run build
```

This runs `tsc && vite build` and outputs the static files to `$UI_DIR/dist`.

**Note:** `npm run dev` starts a live dev server and does **not** produce a `dist/` folder. Always use `npm run build` to generate deployable static assets.

Wait for the build to complete. Verify `dist/` exists and is non-empty:

```bash
ls $UI_DIR/dist/
```

If the build fails, stop and report the error to the user.

---

## Step 5 — Copy dist contents to both targets

Copy every file and folder inside `dist/` to both target directories:

```bash
cp -r $UI_DIR/dist/. $TARGET_INGRESS/
cp -r $UI_DIR/dist/. $TARGET_WEBROOT/
```

Verify the copy:

```bash
ls $TARGET_INGRESS/
ls $TARGET_WEBROOT/assets/ 2>/dev/null | head -5
```

---

## Step 6 — Commit the repo

Get the current VST UI version or latest git commit short SHA to use in the commit message:

```bash
git -C $VIOS_DIR log -1 --format="%h %s"
```

Then stage and commit the changed files. Paths are relative to `VIOS_DIR` (i.e. `services/vios`):

```bash
git -C $VIOS_DIR add deployment/scaling/ingress/vst-ui/ webroot/assets webroot/favicon webroot/index.html
git -C $VIOS_DIR status
git -C $VIOS_DIR log --oneline -3
```

Construct the commit message in this style (matching the repo's commit history):

```
Update VST web UI static assets
```

Or include the source commit if relevant:

```
Update VST web UI static assets from vst-ui-ts <SHORT_SHA>
```

Create the commit:

```bash
git -C $VIOS_DIR commit -m "<COMMIT_MESSAGE>"
```

---

## Step 7 — Report results

Report to the user:
- The resolved `VIOS_DIR` path
- The VST UI build commit/version used
- Confirmation that old assets were removed from both targets
- Confirmation that new dist files were copied to both targets
- The git commit SHA and message created
- Any warnings or errors encountered
- Reminder that the commit has **not** been pushed — run `git -C $VIOS_DIR push` when ready
