"""Re-encode archived Smart City JPEG frames before they are stored.

Frames arrive as whatever JPEG the producer wrote, and SeaweedFS stores image
types as they are (it only gzips compressible types), so the encoding decides
the footprint. SC_FRAME_COMPRESSION picks one of:

- ``off``: store the received bytes.
- ``lossless``: ``jpegtran -copy all -optimize -progressive``. The DCT
  coefficients and markers are kept, so the decoded pixels are identical;
  optimised Huffman tables and progressive scans make the file a few percent
  smaller. Progressive files take longer to decode.
- ``lossy``: ``djpeg | cjpeg -quality Q -optimize -progressive``. Pixels change
  and EXIF/comment markers are dropped; only enable it once training on the
  re-encoded frames has been checked against the same KPI set.

The result replaces the original only when it is smaller. KPI-set frames are
never re-encoded, and a tool that is missing or fails leaves the frame as
received. Compression runs before the object key is derived, so the key still
addresses the stored bytes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from deft_common import Tag

from backend.config import settings

logger = logging.getLogger(__name__)

_JPEG = "image/jpeg"
_SOI = b"\xff\xd8"
_KPI_TAGS = frozenset({Tag.KPI, Tag.KPI_GOLDEN})
_TIMEOUT_S = 30.0

_missing_tool_logged = False


async def _run(argv: list[str], data: bytes) -> bytes:
    """Run one libjpeg-turbo tool with data on stdin; return its stdout."""
    return await _chain([argv], data)


async def _chain(stages: list[list[str]], data: bytes) -> bytes:
    """Run tools connected stdout-to-stdin through OS pipes.

    Intermediate output (the decoded pixels of the lossy mode, hundreds of MB
    for a large frame) passes between the processes and never enters Python.
    Every stage must exit 0: libjpeg-turbo exits 2 on warnings such as a
    truncated file, and whatever it managed to decode must not be stored.
    """
    procs: list[asyncio.subprocess.Process] = []
    open_fds: list[int] = []
    try:
        stdin: int = asyncio.subprocess.PIPE
        for i, argv in enumerate(stages):
            if i == len(stages) - 1:
                stdout: int = asyncio.subprocess.PIPE
                next_stdin = None
            else:
                next_stdin, stdout = os.pipe()
                open_fds += [next_stdin, stdout]
            procs.append(
                await asyncio.create_subprocess_exec(
                    *argv, stdin=stdin, stdout=stdout, stderr=asyncio.subprocess.PIPE
                )
            )
            # The child holds its own copies; keep only the read end the next
            # stage needs until it has been spawned.
            for fd in (stdin, stdout):
                if fd in open_fds:
                    os.close(fd)
                    open_fds.remove(fd)
            if next_stdin is not None:
                stdin = next_stdin
        out, errors = await asyncio.wait_for(_drive(procs, data), _TIMEOUT_S)
    except asyncio.TimeoutError:
        raise RuntimeError(f"{stages[0][0]} timed out after {_TIMEOUT_S:.0f}s") from None
    finally:
        for fd in open_fds:
            os.close(fd)
        for proc in procs:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
    for argv, proc, err in zip(stages, procs, errors):
        if proc.returncode != 0:
            detail = err.decode(errors="replace").strip()[:200]
            raise RuntimeError(f"{argv[0]} exited {proc.returncode}: {detail}")
    return out


async def _drive(
    procs: list[asyncio.subprocess.Process], data: bytes
) -> tuple[bytes, list[bytes]]:
    """Feed the first process and drain the last one's stdout and every
    stderr together, so no pipe fills up; then wait for all to exit."""

    async def feed() -> None:
        stdin = procs[0].stdin
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            stdin.write(data)
            await stdin.drain()
        stdin.close()

    results = await asyncio.gather(
        feed(), procs[-1].stdout.read(), *(p.stderr.read() for p in procs)
    )
    for proc in procs:
        await proc.wait()
    return results[1], list(results[2:])


async def _reencode(data: bytes, mode: str) -> bytes:
    if mode == "lossless":
        return await _run(["jpegtran", "-copy", "all", "-optimize", "-progressive"], data)
    quality = str(settings.sc_frame_jpeg_quality)
    return await _chain(
        [["djpeg"], ["cjpeg", "-quality", quality, "-optimize", "-progressive"]], data
    )


async def compress_frame(data: bytes, content_type: str, tag: str | None) -> bytes:
    """The bytes to store for one frame: re-encoded if smaller, else as given."""
    global _missing_tool_logged
    mode = settings.sc_frame_compression
    if mode == "off" or content_type != _JPEG or tag in _KPI_TAGS:
        return data
    try:
        out = await _reencode(data, mode)
    except FileNotFoundError as exc:
        if not _missing_tool_logged:
            logger.warning("frame compression %s disabled: %s not found", mode, exc.filename)
            _missing_tool_logged = True
        return data
    except (OSError, RuntimeError) as exc:
        # Spawn failures (permissions, process or file-descriptor limits) and
        # tool errors alike: the frame is still stored, as received.
        logger.warning("frame compression %s failed, storing as received: %s", mode, exc)
        return data
    if not out.startswith(_SOI) or len(out) >= len(data):
        return data
    return out
