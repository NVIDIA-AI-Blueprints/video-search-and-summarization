#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render a private Search + realtime Alerts + Filling Compose file; never deploy."""
import argparse, json, os, subprocess
from pathlib import Path
from urllib.parse import urlsplit

def env_values(path):
    result = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key.strip()] = value
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    qa = Path(__file__).resolve().parent
    root = qa.parents[3]
    apps = root / "deploy/docker"
    values = env_values(args.env_file)
    origin = values.get("VSS_PUBLIC_URL", "").rstrip("/")
    url = urlsplit(origin)
    if url.scheme not in {"http", "https"} or not url.hostname or url.username:
        parser.error("VSS_PUBLIC_URL must be a public HTTP(S) origin without credentials")
    if url.path or url.query or url.fragment:
        parser.error("VSS_PUBLIC_URL must not contain a path, query or fragment")
    if not values.get("HOST_IP"):
        parser.error("HOST_IP is required")
    out = args.output.resolve()
    if out.is_relative_to(root):
        parser.error("Output must be outside the source checkout; it can contain credentials")
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    derived = {
        "VSS_APPS_DIR": str(apps), "BUILD_DIR": str(out), "FILLING_QA_DIR": str(qa),
        "EXTERNAL_IP": values["HOST_IP"], "VSS_PUBLIC_HOST": url.hostname,
        "VSS_PUBLIC_PORT": str(url.port or (443 if url.scheme == "https" else 80)),
        "VST_EXTERNAL_URL": origin, "VST_BASE_URL": origin,
        "VSS_AGENT_REPORTS_BASE_URL": origin + "/static/",
        "VSS_AGENT_EXTERNAL_URL": origin, "MEDIA_SERVICE_ENDPOINT": values["HOST_IP"],
        "REACT_APP_API_ENDPOINT_BASE_URL": values["HOST_IP"] + ":8081",
        "VST_CONFIG_PATH": str(apps / "services/vios/configs"),
        "VST_NOTIFICATION_CONFIG_PATH": str(qa / "patches/notification_config.json"),
        "VLM_AS_VERIFIER_CONFIG_FILE": str(apps / "developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/config.yml"),
        "VLM_AS_VERIFIER_CONFIG_FILE_REALTIME": str(apps / "developer-profiles/dev-profile-alerts/vlm-as-verifier/realtime-config.yml"),
        "VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE": str(apps / "developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/alert_type_config.json"),
    }
    generated = out / "paths.env"
    generated.write_text("\n".join(f"{k}={json.dumps(v)}" for k,v in derived.items()) + "\n")
    # Paths inside the include are absolute only in the private generated artifact.
    include = {"include": [{"path": [str(apps/"compose.yml"),
                                    str(qa/"compose.foundation.yml"),
                                    str(qa/"compose.extension.yml")]}]}
    entry = out / "compose.json"
    entry.write_text(json.dumps(include, indent=2)+"\n")
    args_compose = ["docker", "compose"]
    for envfile in [apps/"containers.env", apps/"developer-profiles/dev-profile-search/.env",
                    apps/"developer-profiles/dev-profile-search/overrides.env",
                    qa/"defaults.env", args.env_file.resolve(), generated]:
        args_compose += ["--env-file", str(envfile)]
    args_compose += ["-f", str(entry)]
    resolved = out / "resolved.yml"
    errors = out / "resolve.log"
    with resolved.open("w") as stream, errors.open("w") as log:
        result = subprocess.run(args_compose + ["config", "--no-consistency"], stdout=stream, stderr=log)
    if result.returncode:
        raise SystemExit(f"Compose rendering failed; inspect private {errors}")
    subprocess.run(["python3", str(root/"skills/vss-build-vision-ai/scripts/normalize_resolved_yml.py"),
                    str(resolved)], check=True, stdout=subprocess.DEVNULL)
    with errors.open("a") as log:
        result = subprocess.run(["docker","compose","-f",str(resolved),"config","--quiet"], stdout=log,stderr=log)
    if result.returncode:
        raise SystemExit(f"Compose schema validation failed; inspect private {errors}")
    print(f"Rendered and schema-validated {resolved}. No containers were started.")
    print("Run check-assets.py and the documented setup/acceptance before deployment.")

if __name__ == "__main__":
    main()
