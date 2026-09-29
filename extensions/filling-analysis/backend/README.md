# Filling API

FastAPI serves recorded results and proxies GPU/live workers. Native VSS accesses /filling/ and the harness uses the project CLI. See [the QA recipe](../deployment/qa/README.md).

Dockerfile.runtime includes backend source, locks, calibration and provenance contract. APP_SOURCE_MODE=vss resolves VIOS media. Unsupported source identities fail calibration checks. Neural results require matching model/source hashes.
