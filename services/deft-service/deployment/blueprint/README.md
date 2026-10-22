# DEFT Service deployment provider

This lightweight authoring package advertises the Compose-only real-mode DEFT
microservice through Blueprint Builder's versioned Python entry-point catalog.
It does not install the DEFT application runtime dependencies.

```bash
uv pip install -e ./deployment/blueprint
```

Published dependencies such as `blueprint-builder` are hosted in NVIDIA's
private `nv-shared-pypi-local` repository. Authenticate uv without
putting credentials in the project files, then supply the index when installing
this provider outside the DEFT AOI integration workspace:

```bash
printf '%s' "$ARTIFACTORY_TOKEN" | \
  uv auth login artifactory.nvidia.com \
    --username "$ARTIFACTORY_USER" --password -
uv pip install \
  --index https://artifactory.nvidia.com/artifactory/api/pypi/nv-shared-pypi-local/simple \
  -e ./deployment/blueprint
```

Your account must have read access to `nv-shared-pypi-local`. The integration
workspace resolves all participating provider packages from local checkouts, so
it does not require their wheels to have been published first.

The registered service deliberately supports only the Docker Compose backend:
real-mode DEFT launches sibling TAO containers through the host Docker daemon.
The package exposes `create_component()` for parent integrations. Its
`DeftServiceComponent` contains both the component Blueprint and a typed handle
to the DEFT API service. `create_component_blueprint()` remains as a
compatibility wrapper.

The provider also advertises `deft-aoi-deft-service`. DEFT requires AOI Storage,
so a standalone build must select that connector explicitly rather than
maintaining a second env-patched Blueprint:

```bash
uv run blueprint-builder build deft-service/deft-aoi-deft-service \
  --external-connector storage-backend=aoi-storage.example:8000 \
  --output-dir ./deft-service-deployment
```

Omit the endpoint after `storage-backend` to make its endpoint variables
required deployment inputs. The complete DEFT AOI integration uses the same
factory but supplies the normal internal AOI Storage connection.

Because the provider advertises Docker support only, Blueprint Builder 0.2.3
selects Docker Compose by default and rejects an explicit Helm build. A
successful build also writes its operational deployment/API skill under
`<output-dir>/skills/` unless `--no-deployment-skill` is requested.
