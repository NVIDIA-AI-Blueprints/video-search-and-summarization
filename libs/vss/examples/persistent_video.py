# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Persist local VLM captions and summarize them in a later process.

This example makes real endpoint calls. Configure credentials with environment
variables; it never accepts or prints API keys. See ``tests/storage/README.rst``
for the deterministic, mocked test suite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from vss.ingestion import RequestSource, ingest
from vss.storage import FileStorageAdapter
from vss.summarization import summarize_from_store


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    ingest_parser = commands.add_parser(
        "ingest", help="ingest a local video and save captions to file storage"
    )
    ingest_parser.add_argument("video", type=Path)
    ingest_parser.add_argument("--storage-root", type=Path, required=True)
    ingest_parser.add_argument(
        "--source-id",
        required=True,
        help="stable caller identity; retain it when restarting the job",
    )
    ingest_parser.add_argument(
        "--pipeline-revision",
        required=True,
        help=("revision covering backend defaults, model weights and decoder build"),
    )
    ingest_parser.add_argument("--decoder", choices=("opencv", "pynv"), required=True)
    ingest_parser.add_argument(
        "--model",
        help="defaults to VLM_MODEL; required after environment resolution",
    )
    ingest_parser.add_argument(
        "--chunk-duration",
        type=int,
        default=30,
        help="chunk window length in seconds (default: 30)",
    )
    ingest_parser.add_argument(
        "--job-id",
        type=UUID,
        help="durable UUID; use the reported ID when restarting",
    )
    ingest_parser.add_argument(
        "--resume",
        action="store_true",
        help="resume confirmed chunks for the supplied job ID",
    )

    summary_parser = commands.add_parser(
        "summarize", help="summarize stored captions without video or VLM access"
    )
    summary_parser.add_argument("--storage-root", type=Path, required=True)
    summary_parser.add_argument("--job-id", type=UUID, required=True)
    return parser


async def _ingest(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.resume and args.job_id is None:
        parser.error("--resume requires --job-id")
    video = args.video.expanduser().resolve()
    if not video.is_file():
        parser.error(f"video is not a local file: {video}")
    model = args.model or os.getenv("VLM_MODEL")
    if not model:
        parser.error("pass --model or set VLM_MODEL")

    source = RequestSource(url=str(video), source_id=args.source_id)
    storage_root = args.storage_root.expanduser()
    backend = "vlm"
    async with FileStorageAdapter(root=storage_root) as storage:
        job = ingest(
            source,
            backends=[backend],
            decoder=args.decoder,
            model=model,
            chunk_duration=args.chunk_duration,
            storage=storage,
            job_id=args.job_id,
            resume=args.resume,
            pipeline_revision=args.pipeline_revision,
        )

        # Save these identifiers before awaiting: a failed job may be resumed.
        print(
            json.dumps(
                {"job_id": str(job.id), "source_id": source.source_id},
                separators=(",", ":"),
            ),
            flush=True,
        )
        try:
            chunks = (await job)[backend]
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Exception messages can contain provider data; report only the type.
            print(
                f"ingestion failed ({type(error).__name__}); "
                "retain job_id/source_id; use --resume only if a checkpoint exists",
                file=sys.stderr,
            )
            return 1
        finally:
            await job.wait_closed()

        print(f"stored {len(chunks)} caption chunks for job {job.id}")
    return 0


async def _summarize(args: argparse.Namespace) -> int:
    async with FileStorageAdapter(root=args.storage_root.expanduser()) as storage:
        completion = await summarize_from_store(str(args.job_id), storage=storage)
    payload = json.loads(completion.choices[0].message.content)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


async def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.command == "ingest":
        return await _ingest(args, parser)
    return await _summarize(args)


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    return asyncio.run(_run(args, parser))


if __name__ == "__main__":
    raise SystemExit(main())
