#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check external assets against the reviewed source contracts; never download."""
import argparse, hashlib, importlib.util, json
from uuid import UUID
from pathlib import Path

def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file",type=Path,required=True)
    args=parser.parse_args()
    qa=Path(__file__).resolve().parent
    spec=importlib.util.spec_from_file_location("qa_prepare",qa/"prepare.py")
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    values=mod.env_values(args.env_file)
    calibration=json.loads((qa.parent.parent/"backend/neural_measurement_calibration.json").read_text())
    expected={
      "FILLING_BOTTLE_CHECKPOINT":calibration["model_hashes"]["bottle"],
      "FILLING_LIQUID_CHECKPOINT":calibration["model_hashes"]["liquid"],
      "FILLING_REFERENCE_VIDEO":"21739924f607755906107affa28842df85a4c468e1a8a3a9f28afcb83f346d61"}
    for name,digest in expected.items():
        path=Path(values.get(name,""))
        if not path.is_absolute() or not path.is_file():
            raise SystemExit(f"{name}: an existing absolute asset file is required")
        if sha(path)!=digest:
            raise SystemExit(f"{name}: checksum differs from the reviewed asset")
        print(name+": verified")
    stream_path=Path(values.get("FILLING_STREAMS_FILE",""))
    document=json.loads(stream_path.read_text())
    if document.get("schema_version")!=1 or not isinstance(document.get("streams"),dict):
        raise SystemExit("Invalid streams file")
    if not document["streams"]:
        print("No cameras allowlisted. Base services can start; live inspection cannot start yet.")
    else:
        for stream_id in document["streams"]:
            try: UUID(stream_id)
            except ValueError: raise SystemExit("Replace the stream UUID placeholder with the actual VIOS UUID")
        print("Camera configuration is present; runtime validates its identity and calibration.")
    print("Assets verified. This does not establish GPU or pipeline readiness.")

if __name__=="__main__": main()
