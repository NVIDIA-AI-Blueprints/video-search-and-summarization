#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# System prerequisites for the VIOS + NVStreamer sanity harness: ffmpeg + Google Chrome.
# Debian/Ubuntu (apt / .deb). Python deps and the Playwright browser are installed separately
# (pip install -r requirements.txt ; playwright install chromium) -- or run all of it at once
# with:  python3 services/vios/sanity/run_sanity.py --install-deps
#
# Google Chrome (NOT Playwright's bundled Chromium) is required for WebRTC capture: the WebRTC
# video is H.264/H.265 and the bundled Chromium lacks those proprietary codecs (renders black).
set -euo pipefail

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"
export DEBIAN_FRONTEND=noninteractive

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "[install_deps] ffmpeg"
  $SUDO apt-get update -y
  $SUDO apt-get install -y ffmpeg
else
  echo "[install_deps] ffmpeg already present"
fi

if command -v google-chrome >/dev/null 2>&1 || [ -x /opt/google/chrome/chrome ]; then
  echo "[install_deps] Google Chrome already present"
else
  echo "[install_deps] Google Chrome"
  TMP="$(mktemp -d)"
  wget -qO "$TMP/chrome.deb" \
    https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  $SUDO apt-get install -y "$TMP/chrome.deb" \
    || { $SUDO dpkg -i "$TMP/chrome.deb"; $SUDO apt-get -f install -y; }
  rm -rf "$TMP"
fi

echo "[install_deps] done: ffmpeg=$(command -v ffmpeg || echo MISSING) chrome=$(command -v google-chrome || echo /opt/google/chrome/chrome)"
