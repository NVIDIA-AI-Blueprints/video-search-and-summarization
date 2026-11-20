# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import subprocess
import time
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "src" / "start_rtvi_cv_shared.sh"


def test_wrapper_preserves_config_siblings_and_stops_api(tmp_path):
    config_dir = tmp_path / "configs"
    runtime_dir = tmp_path / "runtime"
    config_dir.mkdir()
    config = config_dir / "ds-main-config.txt"
    config.write_text("http-port=9000\nconfig-file=ds-pgie-config.yml\n")
    (config_dir / "ds-pgie-config.yml").write_text("model: test\n")

    app = tmp_path / "fake-app.sh"
    app.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
config=""
while (($#)); do
    if [[ "$1" == "-c" ]]; then config="$2"; break; fi
    shift
done
test -f "$(dirname "$config")/ds-pgie-config.yml"
grep -q '^http-port=19001$' "$config"
sleep 0.2
exit 7
"""
    )
    app.chmod(0o755)

    api_pid_file = tmp_path / "api.pid"
    api = tmp_path / "fake_api.py"
    api.write_text(
        """import os
import time
from pathlib import Path
Path(os.environ["API_PID_FILE"]).write_text(str(os.getpid()))
while True:
    time.sleep(1)
"""
    )

    env = {
        **os.environ,
        "RTVI_CV_BINARY": str(app),
        "RTVI_CV_SHARED_API": str(api),
        "RTVI_CV_NATIVE_API_PORT": "19001",
        "RTVI_CV_API_PORT": "19000",
        "RTVI_CV_SHARED_RUNTIME_DIR": str(runtime_dir),
        "API_PID_FILE": str(api_pid_file),
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), "-c", str(config)],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 7, result.stderr
    assert (runtime_dir / "ds-pgie-config.yml").is_file()
    api_pid = int(api_pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(api_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    else:
        raise AssertionError(f"API process {api_pid} survived wrapper shutdown")
