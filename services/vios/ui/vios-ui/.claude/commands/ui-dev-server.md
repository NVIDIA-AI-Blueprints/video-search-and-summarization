---
description: Configure backend IP in config.tsx, install deps, and start the UI dev server
argument-hint: <ip:port> [/path]
allowed-tools: AskUserQuestion, Read, Edit, Bash(npm run install:link), Bash(npm run dev)
---

## Task

Start the VST UI dev server pointed at a backend host.

**Arguments provided:** $ARGUMENTS

## Steps

### 1. Determine the backend URL

- If `$ARGUMENTS` is non-empty, parse it as `<ip:port>` with an optional trailing path.
- If no arguments were given, use `AskUserQuestion` to ask the user:
  - "What is the backend IP and port? (e.g. <HOST>:30888)"
- Default path is `/vst` if none provided.
- Construct the full URL: `http://<ip>:<port><path>`

### 2. Edit `src/config.tsx`

Read the file first, then apply **one** of the two cases below:

---

**Case A — file is in original (unedited) state:**

Detect by presence of `isDevelopment` or `getPort` function definitions.

Replace this entire block (from after the import line to before `export default`):

```
const isDevelopment = () => {
    return process.env.NODE_ENV === 'development';
};

const getPort = () => {
    return isDevelopment() ? '30000' : window.location.port;
};

const mdatWebAPIDefaultPort = '8081';
const analyticsUIServerDefaultPort = '8003';

const config: Config = {
    sensorManagementEndpoint: `${window.location.protocol}//${window.location.hostname}:${getPort()}`,
    streamRecorderEndpoint: `${window.location.protocol}//${window.location.hostname}:${getPort()}`,
    storageManagementEndpoint: `${window.location.protocol}//${window.location.hostname}:${getPort()}`,
    liveStreamEndpoint: `${window.location.protocol}//${window.location.hostname}:${getPort()}`,
    replayStreamEndpoint: `${window.location.protocol}//${window.location.hostname}:${getPort()}`,
    streambridgeEndpoint: `${window.location.protocol}//${window.location.hostname}:${getPort()}`,

    mdatWebApiEndpoint: `${window.location.protocol}//${window.location.hostname}:${mdatWebAPIDefaultPort}`,
    analyticsUIServerEndpoint: `${window.location.protocol}//${window.location.hostname}:${analyticsUIServerDefaultPort}`,
    enableLogs: true, // enable-disable console logs
};
```

With:

```
const url = "<CONSTRUCTED_URL>"

const config: Config = {
    sensorManagementEndpoint: url,
    streamRecorderEndpoint: url,
    storageManagementEndpoint: url,
    liveStreamEndpoint: url,
    replayStreamEndpoint: url,
    streambridgeEndpoint: url,

    mdatWebApiEndpoint: url,
    analyticsUIServerEndpoint: url,
    enableLogs: true, // enable-disable console logs
};
```

---

**Case B — file is already in edited state:**

Detect by presence of `const url = "http://...`. 

Just update the URL value on that line:

```
const url = "<CONSTRUCTED_URL>"
```

---

### 3. Install dependencies

Run `npm run install:link` from the project root.

### 4. Start the dev server

Run `npm run dev` from the project root.

Execute steps 2 → 3 → 4 in order.
