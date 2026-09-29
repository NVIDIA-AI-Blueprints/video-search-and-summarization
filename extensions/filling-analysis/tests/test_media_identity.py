# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Actual codec/remux fixtures; execute on the remote host with FFmpeg."""
import hashlib
import subprocess

import pytest

from backend.media_identity import MediaIdentityError, validate_approved_reference, verify_media_identity


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], check=True, capture_output=True)


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    root = tmp_path_factory.mktemp("media_identity")
    reference = root / "reference.mp4"
    remuxed = root / "vios-remux.mp4"
    changed = root / "different-pixels.mp4"
    trimmed = root / "partial.mp4"
    shifted = root / "shifted.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x96:rate=10:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "10", reference)
    ffmpeg("-i", reference, "-map", "0:v:0", "-c", "copy", "-metadata", "comment=VIOS remux test", remuxed)
    ffmpeg("-i", reference, "-vf", "negate", "-c:v", "libx264", "-pix_fmt", "yuv420p", changed)
    ffmpeg("-i", reference, "-t", "1", "-c", "copy", trimmed)
    ffmpeg("-itsoffset", "0.1", "-i", reference, "-c", "copy", shifted)
    return reference, remuxed, changed, trimmed, shifted


def test_identical_bytes_fastpath(clips):
    reference = clips[0]
    proof = verify_media_identity(reference, reference, sha(reference))
    assert proof["verified"]
    assert proof["method"] == "file-sha256"
    assert validate_approved_reference(proof, sha(reference), sha(reference))


def test_remuxed_container_matches_all_encoded_packets_and_config(clips):
    reference, candidate = clips[:2]
    assert sha(reference) != sha(candidate)
    proof = verify_media_identity(candidate, reference, sha(reference))
    assert proof["verified"], proof
    assert proof["packet_count"] == proof["reference_packet_count"] == 20
    assert proof["method"] == "all-encoded-packets-sha256"
    assert proof["time_alignment"] == "exact-zero-offset"
    assert len(proof["sampled_packet_fingerprints"]) == 20
    assert validate_approved_reference(proof, sha(candidate), sha(reference))
    assert not validate_approved_reference(proof, "0" * 64, sha(reference))


def test_unavailable_packet_proof_falls_back_to_every_decoded_frame(clips, monkeypatch):
    from backend import media_identity
    reference, candidate = clips[:2]
    monkeypatch.setattr(media_identity, "_encoded_packets", lambda path: None)
    proof = verify_media_identity(candidate, reference, sha(reference))
    assert proof["verified"]
    assert proof["method"] == "all-decoded-frames-sha256"
    assert proof["frame_count"] == proof["reference_frame_count"] == 20
    assert len(proof["sampled_fingerprints"]) == 20


def test_changed_bitstream_requires_full_decode_even_when_pixels_match(clips, tmp_path):
    reference = clips[0]
    candidate = tmp_path / "aud-inserted.mp4"
    ffmpeg("-i", reference, "-c", "copy", "-bsf:v", "h264_metadata=aud=insert", candidate)
    proof = verify_media_identity(candidate, reference, sha(reference))
    assert proof["verified"]
    assert proof["method"] == "all-decoded-frames-sha256"
    assert proof["frame_count"] == 20


def test_same_geometry_and_duration_do_not_authorize_different_video(clips):
    reference, _, changed, _, _ = clips
    proof = verify_media_identity(changed, reference, sha(reference))
    assert not proof["verified"]
    assert "differs" in proof["reason"]


@pytest.mark.parametrize("index", [3, 4])
def test_trim_and_nonzero_offset_are_rejected_without_guessing(clips, index):
    reference = clips[0]
    proof = verify_media_identity(clips[index], reference, sha(reference))
    assert not proof["verified"]
    assert any(word in proof["reason"] for word in ["offset", "duration", "count", "timestamp"])


def test_changed_reference_fails_closed(clips):
    with pytest.raises(MediaIdentityError, match="differs from the reviewed"):
        verify_media_identity(clips[1], clips[0], "0" * 64)


def test_extra_video_stream_cannot_change_which_pixels_get_analyzed(clips, tmp_path):
    reference = clips[0]
    multistream = tmp_path / "two-videos.mkv"
    ffmpeg("-i", reference, "-i", clips[2], "-map", "0:v", "-map", "1:v", "-c", "copy", multistream)
    with pytest.raises(MediaIdentityError):
        verify_media_identity(multistream, reference, sha(reference))


def test_proof_cannot_authorize_shifted_or_different_current_input(clips):
    reference, candidate = clips[:2]
    proof = verify_media_identity(candidate, reference, sha(reference))
    for patch in [{"verified": False}, {"offset_seconds": .2}, {"reference_sha256": "0" * 64}, {"time_alignment": "guessed"}, {"method": "perceptual-similarity"}]:
        assert not validate_approved_reference({**proof, **patch}, sha(candidate), sha(reference))
    assert not validate_approved_reference(None, sha(candidate), sha(reference))


def test_vision_analyzes_download_bytes_and_keeps_their_identity(clips, monkeypatch):
    from backend import vision
    reference, candidate, changed, _, _ = clips
    proof = verify_media_identity(candidate, reference, sha(reference))
    # A tiny deliberately uncalibrated source exercises the acceptance boundary;
    # no generated imagery or its measurements enters the factory result.
    calibration = {**vision.load_calibration(), "source_sha256": sha(reference),
                   "reference_size": [160, 96], "sample_fps": 5, "source_fps": 10, "scenes": []}
    monkeypatch.setattr(vision, "load_calibration", lambda: calibration)
    result = vision.analyze_video(str(candidate), approved_reference=proof)
    assert result["source_sha256"] == sha(candidate) != sha(reference)
    assert result["calibration_reference_sha256"] == sha(reference)
    assert result["media_identity"] == proof
    assert len(result["samples"]) == 10
    with pytest.raises(ValueError, match="no reviewed calibration"):
        vision.analyze_video(str(changed), approved_reference=proof)
