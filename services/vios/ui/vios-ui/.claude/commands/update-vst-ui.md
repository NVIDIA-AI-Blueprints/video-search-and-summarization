---
description: Build the VST UI and deploy the static files into the vios tree (services/vios of the video-search-and-summarization repo), both ingress/vst-ui and webroot, then commit.
argument-hint: [/path/to/services/vios]
allowed-tools: AskUserQuestion, Read, Bash, Bash(git clone *), Bash(git -C * status), Bash(git -C * add *), Bash(git -C * commit *), Bash(git -C * log *), Bash(npm run install:link), Bash(npm run build), Bash(rm -rf *), Bash(cp -r *)
---

## Task

Build the VST UI and deploy the compiled static assets into the vios component (`services/vios`) of the video-search-and-summarization repository, replacing the old files in both deployment locations, then commit the repo.

**Arguments provided:** $ARGUMENTS

---

## Step 1 — Locate or clone the vios tree

The VST UI deploys into the `vios` component, which lives at `services/vios/` inside the `video-search-and-summarization` monorepo. Resolve `VIOS_DIR` (the path to that `services/vios` directory) using this priority order:

1. **Argument provided** — if `$ARGUMENTS` is non-empty, use that path directly (it should point at the `services/vios` directory). Skip all further checks and go straight to verifying the directory exists.
2. **Current directory** — if `./services/vios` exists (you are at the monorepo root), use it; if the current directory is itself a vios tree (contains `webroot` and `deployment/scaling/ingress`), use `.`.
3. **Default location** — check `~/work/video-search-and-summarization/services/vios`.

If none of (1)–(3) resolves, use `AskUserQuestion` to ask:

> "I couldn't find the vios tree (services/vios). Would you like to provide a path to an existing checkout, or should I clone the video-search-and-summarization monorepo from GitHub? (reply with a path, or type 'clone')"

- If the user supplies a path, use that as `VIOS_DIR`.
- If the user says `clone` (or any variant meaning "go ahead and clone"), clone the monorepo and point `VIOS_DIR` at its `services/vios` directory:

```bash
git clone https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git ~/work/video-search-and-summarization
# VIOS_DIR=~/work/video-search-and-summarization/services/vios
```

Store the resolved path as `VIOS_DIR` for subsequent steps.

The two deployment targets inside `VIOS_DIR` are:
- `TARGET_INGRESS = $VIOS_DIR/deployment/scaling/ingress/vst-ui`
- `TARGET_WEBROOT = $VIOS_DIR/webroot`

---

## Step 2 — Remove old VST UI assets from both targets

Remove only the VST UI files; do **not** touch anything else in `webroot`.

```bash
rm -rf $TARGET_INGRESS/assets $TARGET_INGRESS/favicon $TARGET_INGRESS/index.html
rm -rf $TARGET_WEBROOT/assets $TARGET_WEBROOT/favicon $TARGET_WEBROOT/index.html
```

---

## Step 3 — Install dependencies in the VST UI repo

Run from the `vst-ui-ts` project root (the directory containing `package.json` — the working directory for this Claude session):

```bash
npm run install:link
```

Wait for it to complete before continuing.

---

## Step 4 — Build the VST UI static files

```bash
npm run build
```

This runs `tsc && vite build` and outputs the static files to the `dist/` directory.

**Note:** `npm run dev` starts a live dev server and does **not** produce a `dist/` folder. Always use `npm run build` to generate deployable static assets.

Wait for the build to complete. Verify `dist/` exists and is non-empty:

```bash
ls dist/
```

If the build fails, stop and report the error to the user.

---

## Step 5 — Copy dist contents to both targets

Copy every file and folder inside `dist/` to both target directories:

```bash
cp -r dist/. $TARGET_INGRESS/
cp -r dist/. $TARGET_WEBROOT/
```

Verify the copy:

```bash
ls $TARGET_INGRESS/
ls $TARGET_WEBROOT/assets/ 2>/dev/null | head -5
```

---

## Step 6 — Commit the repo

Get the current VST UI version or latest git commit short SHA from the VST UI repo to use in the commit message:

```bash
git log -1 --format="%h %s"
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
- Whether the vios tree was found locally or the monorepo was cloned, and the resolved `VIOS_DIR` path
- The VST UI build commit/version used
- Confirmation that old assets were removed from both targets
- Confirmation that new dist files were copied to both targets
- The git commit SHA and message created
- Any warnings or errors encountered
- Reminder that the commit has **not** been pushed — run `git -C $VIOS_DIR push` when ready
