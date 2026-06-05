#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Setup script to check and install system dependencies."""

import subprocess
import sys
import shutil


def check_jpeginfo() -> bool:
    """Check if jpeginfo is installed."""
    return shutil.which('jpeginfo') is not None


def check_mediainfo() -> bool:
    """Check if mediainfo is installed."""
    return shutil.which('mediainfo') is not None


def check_ffmpeg() -> bool:
    """Check if ffmpeg is installed."""
    return shutil.which('ffmpeg') is not None


def check_av_libraries() -> bool:
    """Check if required libav libraries for aiortc are installed."""
    try:
        result = subprocess.run(
            ['pkg-config', '--exists', 'libavformat', 'libavcodec', 'libavutil'],
            capture_output=True
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def check_apt_available() -> bool:
    """Check if apt-get is available."""
    return shutil.which('apt-get') is not None


def check_dependencies() -> None:
    """Check if all system dependencies are installed."""
    print("Checking system dependencies...")
    
    jpeginfo_installed = check_jpeginfo()
    mediainfo_installed = check_mediainfo()
    ffmpeg_installed = check_ffmpeg()
    av_libraries_installed = check_av_libraries()
    
    if jpeginfo_installed:
        print("✓ jpeginfo is installed")
    else:
        print("✗ jpeginfo is NOT installed")
    
    if mediainfo_installed:
        print("✓ mediainfo is installed")
    else:
        print("✗ mediainfo is NOT installed")
    
    if ffmpeg_installed:
        print("✓ ffmpeg is installed")
    else:
        print("✗ ffmpeg is NOT installed")
    
    if av_libraries_installed:
        print("✓ libav libraries for aiortc are installed")
    else:
        print("✗ libav libraries for aiortc are NOT installed")
    
    if jpeginfo_installed and mediainfo_installed and ffmpeg_installed and av_libraries_installed:
        sys.exit(0)
    else:
        print("\nPlease install missing dependencies:")
        if not jpeginfo_installed:
            print("  sudo apt-get install jpeginfo")
        if not mediainfo_installed:
            print("  sudo apt-get install mediainfo")
        if not ffmpeg_installed:
            print("  sudo apt-get install ffmpeg")
        if not av_libraries_installed:
            print("  sudo apt-get install libavformat-dev libavcodec-dev libavdevice-dev libavutil-dev libswscale-dev libswresample-dev libavfilter-dev libopus-dev libvpx-dev")
        print("\nOr run: poetry run setup-system-deps")
        sys.exit(1)


def main() -> None:
    """Main setup function to install system dependencies for Ubuntu/Debian."""
    print("VST BDD Tests - System Dependencies Setup")
    print("=" * 50)
    
    jpeginfo_installed = check_jpeginfo()
    mediainfo_installed = check_mediainfo()
    ffmpeg_installed = check_ffmpeg()
    av_libraries_installed = check_av_libraries()
    
    if jpeginfo_installed and mediainfo_installed and ffmpeg_installed and av_libraries_installed:
        print("✓ All dependencies are already installed")
        return
    
    packages_to_install = []
    if not jpeginfo_installed:
        print("✗ jpeginfo is not installed")
        packages_to_install.append('jpeginfo')
    else:
        print("✓ jpeginfo is already installed")
    
    if not mediainfo_installed:
        print("✗ mediainfo is not installed")
        packages_to_install.append('mediainfo')
    else:
        print("✓ mediainfo is already installed")
    
    if not ffmpeg_installed:
        print("✗ ffmpeg is not installed")
        packages_to_install.append('ffmpeg')
    else:
        print("✓ ffmpeg is already installed")
    
    if not av_libraries_installed:
        print("✗ libav libraries for aiortc are not installed")
        packages_to_install.extend([
            'libavformat-dev', 'libavcodec-dev', 'libavdevice-dev',
            'libavutil-dev', 'libswscale-dev', 'libswresample-dev',
            'libavfilter-dev', 'libopus-dev', 'libvpx-dev'
        ])
    else:
        print("✓ libav libraries for aiortc are already installed")
    
    if not check_apt_available():
        print("\n✗ apt-get not found. This script only supports Ubuntu/Debian.")
        print("\nPlease install dependencies manually:")
        for pkg in packages_to_install:
            print(f"  sudo apt-get install {pkg}")
        sys.exit(1)
    
    print(f"\nAttempting to install {', '.join(packages_to_install)} using apt-get...")
    print("This requires sudo access.")
    
    install_cmd = ['sudo', 'apt-get', 'install', '-y'] + packages_to_install
    print(f"Running: {' '.join(install_cmd)}")
    
    response = input("Continue? [y/N]: ").strip().lower()
    if response not in ('y', 'yes'):
        print("Installation cancelled.")
        sys.exit(1)
    
    try:
        subprocess.run(
            install_cmd,
            check=True,
            capture_output=True,
            text=True
        )
        print(f"\n✓ {', '.join(packages_to_install)} installed successfully!")
        
        all_verified = True
        if 'jpeginfo' in packages_to_install:
            if check_jpeginfo():
                print("✓ Verified: jpeginfo is now available")
            else:
                print("⚠ Warning: jpeginfo was installed but not found in PATH")
                all_verified = False
        
        if 'mediainfo' in packages_to_install:
            if check_mediainfo():
                print("✓ Verified: mediainfo is now available")
            else:
                print("⚠ Warning: mediainfo was installed but not found in PATH")
                all_verified = False
        
        if 'ffmpeg' in packages_to_install:
            if check_ffmpeg():
                print("✓ Verified: ffmpeg is now available")
            else:
                print("⚠ Warning: ffmpeg was installed but not found in PATH")
                all_verified = False
        
        if 'libavformat-dev' in packages_to_install:
            if check_av_libraries():
                print("✓ Verified: libav libraries for aiortc are now available")
            else:
                print("⚠ Warning: libav libraries were installed but not detected")
                all_verified = False
        
        if not all_verified:
            print("  You may need to restart your terminal")
    except subprocess.CalledProcessError as e:
        print(f"\n✗ Failed to install dependencies: {e}")
        print(f"\nError output: {e.stderr}")
        print("\nPlease install manually:")
        for pkg in packages_to_install:
            print(f"  sudo apt-get install {pkg}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

