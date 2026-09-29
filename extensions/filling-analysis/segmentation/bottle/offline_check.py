# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check that explicit cached-checkpoint inference does not use the network."""
import argparse
import json
import socket
import cv2
from segmenter import BottleSegmenter

parser = argparse.ArgumentParser()
parser.add_argument("--checkpoint", required=True)
parser.add_argument("--frame", required=True)
args = parser.parse_args()
attempts = []
original_connect = socket.socket.connect
def no_internet(sock, address):
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        attempts.append(str(address))
        raise RuntimeError("Offline-check denied network connection")
    return original_connect(sock, address)
socket.socket.connect = no_internet
model = BottleSegmenter(checkpoint=args.checkpoint)
instances = model.predict(cv2.imread(args.frame))
assert not attempts
print(json.dumps({"offline_network_attempts": attempts, "bottles": len(instances),
                  "model": model.identity, "load_seconds": model.load_seconds,
                  "timing": model.last_timing}))
