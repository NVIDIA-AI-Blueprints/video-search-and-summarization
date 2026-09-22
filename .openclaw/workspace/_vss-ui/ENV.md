# ENV.md - VSS UI Environment

The values below are rendered when the sandbox is connected to a deployment.
Use them as data; this agent has no shell startup step.

```text
export VSS_PUBLIC_URL=""
export HITL_ENABLED=false
```

If `VSS_PUBLIC_URL` is empty, ask the user for the deployment origin. Do not
guess or probe for it.
