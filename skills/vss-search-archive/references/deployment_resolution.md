# Deployment resolution for `vss-cli`

The host CLI owns deployment discovery. It does not call the VSS agent runtime
endpoint and it does not need a shell inside a container or pod.

## Docker

Use `--deployment docker --profile <profile>`. The profile must have both its
checked-in `.env` and a runtime `generated.env` created by `dev-profile.sh`.
The command first reads the shared VST/RTVI service defaults used by the search
profile, then reads `.env` and overlays `generated.env`, matching Docker Compose
precedence. It expands that effective environment with the checked-out profile
config and maps private Compose service addresses to their loopback-published
ports. The checked-in `.env` supplies stable values, but it is not sufficient
by itself because `generated.env` proves deployment initialization and supplies
mutable overrides.

## Kubernetes

Use `--deployment kubernetes --namespace <namespace> --release <release>`.
The command reads the live vss-agent Deployment to find the config mount and
literal/configMap environment values, reads the mounted `config.yml` from the
live ConfigMap, and reads only allow-listed non-secret keys from referenced
ConfigMaps. It does not read `values.yaml`, `secretKeyRef`, `envFrom` Secrets,
or the agent runtime endpoint.

Private backend Service endpoints are port-forwarded to managed localhost
ports. `VST_EXTERNAL_URL` is deliberately not: screenshot and media links in
the result must remain usable after the command closes its managed forwards.
That field must be a host-reachable ingress URL or an operator-managed
localhost forward with a sufficiently long lifetime; a Service-backed value is
rejected. Other external endpoints remain unchanged. The host must have RBAC
to read Deployments, ConfigMaps, and Services and to create port-forwards.

## Precedence and secret boundary

Explicit runtime flags override discovered values. A non-secret
`--config-env KEY=VALUE` override takes precedence over discovered interpolation
values. If a required runtime value is Secret-backed, discovery fails with
remediation rather than leaking or consuming the secret.
