# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fail-closed identity verification for a VIOS-downloaded full recording.

Container hashes may change during remuxing. Accept byte-identical media, every
encoded packet plus decoder configuration/timing, or every decoded video frame.
Partial clips, preroll, timestamp shifts and perceptual matches are unsupported.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from fractions import Fraction
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

VERIFICATION_VERSION = "video-identity-v2"


class MediaIdentityError(ValueError):
    """Media cannot be safely associated with the reviewed calibration."""


def _sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _probe(path: Path) -> dict:
    try:
        output = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
             "stream=width,height,pix_fmt,color_range,color_space,color_transfer,color_primaries,sample_aspect_ratio,avg_frame_rate,r_frame_rate,start_time,duration,nb_frames:format=duration",
             "-of", "json", str(path)],
            check=True, capture_output=True, timeout=45,
        )
        data = json.loads(output.stdout)
        if len(data["streams"]) != 1:
            raise MediaIdentityError("Identity verification requires exactly one video stream")
        stream = data["streams"][0]
        result = {
            "width": int(stream["width"]), "height": int(stream["height"]),
            "fps": str(Fraction(stream["avg_frame_rate"])),
            "nominal_fps": str(Fraction(stream["r_frame_rate"])),
            "start_time": float(stream.get("start_time", 0)),
            "duration": float(stream.get("duration", data["format"]["duration"])),
            "pixel_format": stream["pix_fmt"],
        }
        for field in ("color_range", "color_space", "color_transfer", "color_primaries", "sample_aspect_ratio"):
            value = stream.get(field)
            result[field] = None if value in (None, "unknown", "N/A", "0:1") else value
        if result["width"] <= 0 or result["height"] <= 0 or Fraction(result["fps"]) <= 0:
            raise ValueError("Nonpositive video geometry/cadence")
        if not all(math.isfinite(result[k]) for k in ("start_time", "duration")) or result["duration"] <= 0:
            raise ValueError("Invalid video timing")
        return result
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, ZeroDivisionError) as exc:
        raise MediaIdentityError("Video identity metadata could not be verified") from exc


def _decoded_frames(path: Path) -> tuple[tuple[str, str], ...]:
    """Return exact (source PTS seconds, decoded pixel SHA256) for every frame."""
    try:
        output = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-threads", "4", "-copyts",
             "-i", str(path), "-map", "0:v:0", "-an", "-sn", "-dn", "-vsync", "0",
             "-c:v", "rawvideo", "-threads", "4", "-f", "framemd5", "-hash", "sha256", "pipe:1"],
            check=True, capture_output=True, timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MediaIdentityError("Full decoded-video identity check failed") from exc
    time_base = None
    frames = []
    for line in output.stdout.decode("utf-8").splitlines():
        if line.startswith("#tb 0:"):
            time_base = Fraction(line.split(":", 1)[1].strip())
        elif line and not line.startswith("#"):
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 6 or time_base is None:
                raise MediaIdentityError("Unexpected decoded-frame fingerprint format")
            timestamp = str(int(parts[2]) * time_base)
            digest = parts[5]
            if len(digest) != 64:
                raise MediaIdentityError("Decoded-frame SHA256 is missing")
            frames.append((timestamp, digest))
    if not frames:
        raise MediaIdentityError("Video did not decode to any frames")
    return tuple(frames)


def _encoded_packets(path: Path) -> tuple[dict, tuple] | None:
    """Exact decoder inputs, independent of container layout/time-base units.

    This is a sufficient identity proof only when *all* compressed payloads,
    codec extradata/interpretation, timestamps and packet flags agree. Unknown
    side data or absent metadata decline the shortcut and use decoded checking.
    """
    try:
        output = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
             "-show_packets", "-show_data_hash", "sha256", "-of", "json", str(path)],
            check=True, capture_output=True, timeout=90,
        )
        data = json.loads(output.stdout)
        stream = data["streams"][0]
        extradata = stream.get("extradata_hash", "")
        if not extradata.startswith("SHA256:") or len(extradata) != 71 or stream.get("side_data_list"):
            return None
        fields = ("codec_name", "codec_tag_string", "profile", "level", "width", "height",
                  "coded_width", "coded_height", "pix_fmt", "field_order", "chroma_location",
                  "color_range", "color_space", "color_transfer", "color_primaries",
                  "sample_aspect_ratio", "bits_per_raw_sample", "has_b_frames", "refs",
                  "is_avc", "nal_length_size", "extradata_hash")
        decoder = {field: stream.get(field) for field in fields}
        # MP4 remuxing may omit the optional container field-order hint. H.264
        # field coding is determined by the identical SPS/PPS and slice bytes;
        # our decoder applies no deinterlacing. Keep all other cases strict.
        if stream.get("codec_name") == "h264" and decoder["field_order"] in (None, "unknown", "progressive"):
            decoder["field_order"] = "h264-bitstream-defined"
        time_base = Fraction(stream["time_base"])
        packets = []
        for packet in data["packets"]:
            digest = packet.get("data_hash", "")
            if packet.get("side_data_list") or not digest.startswith("SHA256:") or len(digest) != 71:
                return None
            pts = str(int(packet["pts"]) * time_base)
            dts = str(int(packet["dts"]) * time_base)
            duration = str(int(packet["duration"]) * time_base)
            if Fraction(duration) <= 0:
                return None
            packets.append((pts, dts, duration, int(packet["size"]), packet["flags"], digest[7:]))
        return (decoder, tuple(packets)) if packets else None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, ZeroDivisionError):
        return None


@lru_cache(maxsize=4)
def _reference_packets(path: str, digest: str, stat_signature: tuple) -> tuple[dict, tuple] | None:
    return _encoded_packets(Path(path))


@lru_cache(maxsize=4)
def _reference_frames(path: str, digest: str, stat_signature: tuple) -> tuple[tuple[str, str], ...]:
    # digest is re-read/checked before every call. The cache cannot authorize a
    # changed reference merely because a filename or mtime matches.
    return _decoded_frames(Path(path))


def _signature(path: Path) -> tuple[int, int, int]:
    value = path.stat()
    return value.st_ino, value.st_size, value.st_mtime_ns


def verify_media_identity(candidate_path: str | Path, reference_path: str | Path,
                          expected_reference_sha256: str) -> dict:
    """Authorize zero-offset calibration only for exact full-video identity.

    Call this internally after downloading the selected VIOS source. Do not
    accept a proof supplied by an API client. ``verified=False`` is a controlled
    mismatch; inability to inspect/decode raises ``MediaIdentityError``.
    """
    candidate, reference = Path(candidate_path), Path(reference_path)
    if not candidate.is_file() or not reference.is_file():
        raise MediaIdentityError("Candidate and reviewed reference must both exist")
    if len(expected_reference_sha256) != 64:
        raise MediaIdentityError("Expected reviewed-reference SHA256 is required")
    before_candidate, before_reference = _signature(candidate), _signature(reference)
    reference_sha = _sha(reference)
    if reference_sha != expected_reference_sha256:
        raise MediaIdentityError("Reference file differs from the reviewed calibration source")
    candidate_sha = _sha(candidate)
    proof = {"version": VERIFICATION_VERSION, "verified": False,
             "candidate_sha256": candidate_sha, "reference_sha256": reference_sha,
             "offset_seconds": 0.0, "time_alignment": "unverified",
             "method": None, "reason": "Unverified", "frame_count": None}
    if candidate_sha == reference_sha:
        proof.update(verified=True, method="file-sha256", time_alignment="exact-zero-offset",
                     reason="Downloaded media is byte-identical to the reviewed reference")
    else:
        candidate_meta, reference_meta = _probe(candidate), _probe(reference)
        proof.update(candidate_metadata=candidate_meta, reference_metadata=reference_meta)
        fields = ("width", "height", "fps", "nominal_fps", "pixel_format", "color_range", "color_space", "color_transfer", "color_primaries", "sample_aspect_ratio")
        differences = [field for field in fields if candidate_meta[field] != reference_meta[field]]
        if differences:
            proof["reason"] = "Video metadata differs: " + ", ".join(differences)
            return proof
        tolerance = min(.001, 1 / float(Fraction(reference_meta["fps"])) / 20)
        if abs(candidate_meta["start_time"] - reference_meta["start_time"]) > tolerance:
            proof["reason"] = "Nonzero video start offset; shifted or preroll clips require explicit alignment and are not accepted"
            return proof
        if abs(candidate_meta["duration"] - reference_meta["duration"]) > tolerance:
            proof["reason"] = "Full recording duration differs; trimmed or partial clips are not accepted"
            return proof
        with ThreadPoolExecutor(max_workers=2) as pool:
            reference_packet_future = pool.submit(_reference_packets, str(reference.resolve()), reference_sha, before_reference)
            candidate_packets = _encoded_packets(candidate)
            reference_packets = reference_packet_future.result()
        if candidate_packets is not None and candidate_packets == reference_packets:
            decoder, packets = candidate_packets
            chain = hashlib.sha256(json.dumps(candidate_packets, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            indices = sorted({round(i * (len(packets) - 1) / 24) for i in range(25)})
            proof.update(verified=True, method="all-encoded-packets-sha256", time_alignment="exact-zero-offset",
                         reason="All encoded video payloads, decoder configuration and timestamps match the complete reviewed recording",
                         packet_count=len(packets), reference_packet_count=len(packets),
                         codec_extradata_sha256=decoder["extradata_hash"][7:], packet_sequence_sha256=chain,
                         sampled_packet_fingerprints=[{"packet": i, "pts_seconds": packets[i][0], "dts_seconds": packets[i][1], "duration_seconds": packets[i][2], "sha256": packets[i][5]} for i in indices])
            if _signature(candidate) != before_candidate or _signature(reference) != before_reference:
                raise MediaIdentityError("Media changed during identity verification; retry after the download finishes")
            return proof
        with ThreadPoolExecutor(max_workers=2) as pool:
            reference_future = pool.submit(_reference_frames, str(reference.resolve()), reference_sha, before_reference)
            candidate_frames = _decoded_frames(candidate)
            reference_frames = reference_future.result()
        proof["frame_count"] = len(candidate_frames)
        proof["reference_frame_count"] = len(reference_frames)
        if len(candidate_frames) != len(reference_frames):
            proof["reason"] = "Decoded frame count differs from the complete reviewed recording"
            return proof
        mismatch = next((i for i, pair in enumerate(zip(candidate_frames, reference_frames)) if pair[0] != pair[1]), None)
        if mismatch is not None:
            proof["reason"] = "Decoded image or source timestamp differs; approximate visual similarity is not accepted"
            proof["first_mismatch_frame"] = mismatch
            return proof
        chain = hashlib.sha256("\n".join(f"{t}:{sha}" for t, sha in candidate_frames).encode()).hexdigest()
        indices = sorted({round(i * (len(candidate_frames) - 1) / 24) for i in range(25)})
        proof.update(verified=True, method="all-decoded-frames-sha256", time_alignment="exact-zero-offset",
                     reason="Every decoded image and timestamp matches the complete reviewed recording",
                     decoded_sequence_sha256=chain,
                     sampled_fingerprints=[{"frame": i, "t": float(Fraction(candidate_frames[i][0])), "sha256": candidate_frames[i][1]} for i in indices])
    if _signature(candidate) != before_candidate or _signature(reference) != before_reference:
        raise MediaIdentityError("Media changed during identity verification; retry after the download finishes")
    return proof


def validate_approved_reference(proof: dict | None, candidate_sha256: str, expected_reference_sha256: str) -> bool:
    """Check an internally obtained proof against current candidate bytes."""
    return bool(
        isinstance(proof, dict)
        and proof.get("version") == VERIFICATION_VERSION
        and proof.get("verified") is True
        and proof.get("candidate_sha256") == candidate_sha256
        and proof.get("reference_sha256") == expected_reference_sha256
        and proof.get("time_alignment") == "exact-zero-offset"
        and type(proof.get("offset_seconds")) in (int, float)
        and proof["offset_seconds"] == 0
        and proof.get("method") in {"file-sha256", "all-encoded-packets-sha256", "all-decoded-frames-sha256"}
        and (proof.get("method") != "file-sha256" or candidate_sha256 == expected_reference_sha256)
    )
