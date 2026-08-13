# Deployment origin contract

The deployment workflow publishes one operator-facing origin as
`VSS_PUBLIC_URL`, including its scheme and any non-default port. This search
skill consumes that value; it does not discover Helm releases, Kubernetes
Services, NodePorts, or hostnames.

```bash
: "${VSS_PUBLIC_URL:?use the origin published by the deployment workflow}"
VSS_PUBLIC_URL="${VSS_PUBLIC_URL%/}"
AGENT_URL="${VSS_PUBLIC_URL}"
VSS_VIOS_URL="${VSS_PUBLIC_URL}/vst"
VST_API_BASE="${VSS_VIOS_URL}/api/v1"
```

For the Kubernetes search profile, source listing uses
`${VST_API_BASE}/sensor/list`; Agent search, ingestion, and deletion use the
same public origin. Record the origin once with the project-local
`vss configure --base-url` command. Search paths use the routes recorded by
that command and exit 4 if a required route is absent.

Never invent a Brev or nip.io hostname. Never substitute port-forwarding,
Service DNS, a NodePort, localhost, or reconstructed media URLs. Returned clip
and screenshot URLs must match the origin recorded by `vss configure`.

Compose follows the same configure contract, normally using its host-reachable
HAProxy origin. On Brev, use the bundled `select_brev_origin.sh` exactly once
to choose between the deployment-minted public HTTPS origin and the documented
host-reachable fallback.
