# License text overrides

Text used by `.github/scripts/generate_python_third_party_licenses.py` for
distributions whose wheel ships no license file of its own. The generator raises
rather than guessing, which is the correct default — this directory is how a
known-good text is supplied deliberately.

## opentelemetry-util-http

The only distribution in this service's dependency set with no bundled license.
It is published from `open-telemetry/opentelemetry-python-contrib`, the same
repository and the same 0.65b0 release as `opentelemetry-instrumentation`,
`-asgi`, `-fastapi`, `-httpx` and `-requests`, all of which bundle a license.
Those five bundled files are byte-identical to each other (md5 86d3f3a95c32),
and this file is a copy of that text: the Apache License 2.0.

Sourced from the sibling wheel rather than transcribed, so it is verifiable
against the artifact rather than trusted.
