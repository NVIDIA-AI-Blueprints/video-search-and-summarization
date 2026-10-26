# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Plan, run or export the portable E2E performance matrix."""

import argparse
import json
from pathlib import Path

from benchmarks.matrix.config import PLACEMENTS, PROFILES, Matrix, load, select


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "export"))
    parser.add_argument(
        "--recipe", type=Path, default=Path(__file__).with_name("recipe.toml")
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="New run directory, or an existing directory for export",
    )
    parser.add_argument("--profiles", help="Comma-separated names, or all")
    parser.add_argument("--exclude-profiles", default="")
    parser.add_argument(
        "--placements",
        help=(
            "local-local,local-remote,remote-local,remote-remote, or all (VLM-"
            "LLM order)"
        ),
    )
    parser.add_argument("--exclude-placements", default="")
    parser.add_argument(
        "--workloads", help="Comma-separated recipe workload names, or all"
    )
    parser.add_argument("--chunk-durations", help="Comma-separated seconds")
    parser.add_argument("--frames", help="Comma-separated requested frames per chunk")
    parser.add_argument("--streams", help="Comma-separated concurrent streams")
    for name in ("repetitions", "warmups", "max-trials"):
        parser.add_argument("--" + name, type=int)
    parser.add_argument(
        "--run-timeout",
        type=float,
        help="Wall admission deadline in seconds, followed by bounded cleanup",
    )
    args = parser.parse_args()
    try:
        if args.command == "export":
            if args.output is None:
                parser.error("export requires --output")
            from benchmarks.matrix.export import export

            rows = export(args.output.resolve())
            print(f"Exported {len(rows)} trials to {args.output}/results.xlsx")
            return 0
        recipe = load(args.recipe)
        updates = dict(
            profiles=select(
                args.profiles,
                PROFILES if args.profiles else recipe.profiles,
                args.exclude_profiles.split(",") if args.exclude_profiles else (),
            ),
            placements=select(
                args.placements,
                PLACEMENTS if args.placements else recipe.placements,
                args.exclude_placements.split(",") if args.exclude_placements else (),
            ),
        )
        names = select(args.workloads, [m.name for m in recipe.media])
        updates["media"] = tuple(m for m in recipe.media if m.name in names)
        for flag, field in (
            (args.chunk_durations, "chunk_durations"),
            (args.frames, "frames_per_chunk"),
            (args.streams, "streams"),
        ):
            if flag is not None:
                updates[field] = tuple(int(v) for v in flag.split(","))
        for field in ("repetitions", "warmups", "max_trials"):
            if getattr(args, field) is not None:
                updates[field] = getattr(args, field)
        if args.run_timeout is not None:
            updates["run_timeout_s"] = args.run_timeout
        recipe = Matrix.model_validate(recipe.model_dump() | updates)
        from benchmarks.matrix.runner import plan, run

        if args.command == "plan":
            print(json.dumps(plan(recipe), indent=2))
            return 0
        if args.output is None:
            parser.error("run requires a new --output directory")
        result = run(recipe, args.output.resolve())
        print(f"{result['status']}: {args.output}/results.xlsx")
        return (
            0
            if result["status"] == "finished"
            and all(t["status"] == "completed_diagnostic" for t in result["trials"])
            else 1
        )
    except Exception as error:
        # Avoid leaking private endpoints, request content or credentials.
        print(
            f"Performance command failed ({type(error).__name__}); "
            "inspect private receipts and recipe."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
