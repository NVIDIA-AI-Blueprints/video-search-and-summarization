# VSS Orchestrator

Use native VSS Orchestrator MCP tools when they are configured. An optional command bridge can also perform the MCP Streamable HTTP handshake and discover the live tool catalog. Set `VSS_ORCHESTRATOR_BRIDGE` to its executable path; the managed setup installs it at `/sandbox/bin/vss-orchestrator`.

## OpenShell Sandboxes

When running inside an OpenShell sandbox, reach VSS services on the host through `host.openshell.internal`. Do not use `localhost`, a host LAN address, or a literal container IP: the sandbox egress policy only permits the host alias on approved VSS ports. Set `HOST_IP=host.openshell.internal` when a command or skill expects that variable.

Outside OpenShell, use the VSS endpoint configured for that Hermes environment.

When the command bridge is available, list tools before an unfamiliar operation:

```bash
"${VSS_ORCHESTRATOR_BRIDGE:-/sandbox/bin/vss-orchestrator}" list
```

Call a tool by its short name. Supply inputs as one JSON object when required:

```bash
"${VSS_ORCHESTRATOR_BRIDGE:-/sandbox/bin/vss-orchestrator}" profiles
"${VSS_ORCHESTRATOR_BRIDGE:-/sandbox/bin/vss-orchestrator}" docker_generate '{"profile":"base"}'
```

The VSS Orchestrator MCP server must be running before use.
