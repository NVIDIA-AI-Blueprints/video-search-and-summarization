---
description: Build the VST UI and deploy the static files into the VIOS/vms_shim repo (both ingress/vst-ui and webroot), then commit the vms_shim repo.
argument-hint: [/path/to/vms_shim]
allowed-tools: AskUserQuestion, Read, Bash, Bash(git clone *), Bash(git -C * status), Bash(git -C * add *), Bash(git -C * commit *), Bash(git -C * log *), Bash(npm run install:link), Bash(npm run build), Bash(rm -rf *), Bash(cp -r *)
---

## Task

Build the VST UI and deploy the compiled static assets into the VIOS/vms_shim repository, replacing the old files in both deployment locations, then commit the vms_shim repo.

**Arguments provided:** $ARGUMENTS

---

## Step 1 — Locate or clone the vms_shim repo

Resolve `VMS_SHIM_DIR` using this priority order:

1. **Argument provided** — if `$ARGUMENTS` is non-empty, use that path directly. Skip all further checks and go straight to verifying the directory exists.
2. **Current directory** — check whether a `vms_shim` directory exists inside the current working directory (`./vms_shim`).
3. **Default location** — check `~/work/vms_shim` (i.e. `/home/$USER/work/vms_shim`).

If neither location (2) nor (3) exists, use `AskUserQuestion` to ask:

> "I couldn't find the vms_shim repo. Would you like to provide a path to an existing clone, or should I clone it from GitLab? (reply with a path, or type 'clone')"

- If the user supplies a path, use that as `VMS_SHIM_DIR`.
- If the user says `clone` (or any variant meaning "go ahead and clone"), clone into `~/work/vms_shim`:

```bash
git clone ssh://git@gitlab-master.nvidia.com:12051/L4TMM/vms_shim.git ~/work/vms_shim
```

Store the resolved path as `VMS_SHIM_DIR` for subsequent steps.

The two deployment targets inside `VMS_SHIM_DIR` are:
- `TARGET_INGRESS = $VMS_SHIM_DIR/deployment/scaling/ingress/vst-ui`
- `TARGET_WEBROOT = $VMS_SHIM_DIR/webroot`

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

## Step 6 — Commit the vms_shim repo

Get the current VST UI version or latest git commit short SHA from the VST UI repo to use in the commit message:

```bash
git log -1 --format="%h %s"
```

Then stage and commit the changed files in vms_shim:

```bash
git -C $VMS_SHIM_DIR add deployment/scaling/ingress/vst-ui/ webroot/assets webroot/favicon webroot/index.html
git -C $VMS_SHIM_DIR status
git -C $VMS_SHIM_DIR log --oneline -3
```

Construct the commit message in this style (matching the vms_shim commit history):

```
Update VST web UI static assets
```

Or include the source commit if relevant:

```
Update VST web UI static assets from vst-ui-ts <SHORT_SHA>
```

Create the commit:

```bash
git -C $VMS_SHIM_DIR commit -m "<COMMIT_MESSAGE>"
```

---

## Step 7 — Report results

Report to the user:
- Whether the vms_shim repo was cloned or already present, and its path
- The VST UI build commit/version used
- Confirmation that old assets were removed from both targets
- Confirmation that new dist files were copied to both targets
- The vms_shim git commit SHA and message created
- Any warnings or errors encountered
- Reminder that the commit has **not** been pushed — run `git -C $VMS_SHIM_DIR push` when ready
