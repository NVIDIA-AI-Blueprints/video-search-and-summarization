#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail
umask 077

sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wireguard-tools
sudo install -d -m 0700 -o root -g root /etc/wireguard
if ! sudo test -f /etc/wireguard/vss-skill-eval.key; then
    wg genkey | sudo tee /etc/wireguard/vss-skill-eval.key >/dev/null
fi
sudo chmod 0600 /etc/wireguard/vss-skill-eval.key
sudo cat /etc/wireguard/vss-skill-eval.key | wg pubkey
