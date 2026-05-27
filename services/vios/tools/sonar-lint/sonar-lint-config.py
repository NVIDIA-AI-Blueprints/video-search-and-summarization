#!/usr/bin/env python3
#
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

"""
SonarLint Configuration Script
Automates configuration of SonarLint settings for Cursor/VS Code
Note: Extension must be installed manually via GUI before running this script
"""

import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ANSI color codes
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color

def print_success(msg):
    print(f"{Colors.GREEN}✓ {msg}{Colors.NC}")

def print_error(msg):
    print(f"{Colors.RED}✗ {msg}{Colors.NC}")

def print_info(msg):
    print(f"{Colors.BLUE}ℹ {msg}{Colors.NC}")

def print_warning(msg):
    print(f"{Colors.YELLOW}⚠ {msg}{Colors.NC}")

def print_header():
    print(f"\n{Colors.BLUE}{'='*44}{Colors.NC}")
    print(f"{Colors.BLUE}   SonarLint Configuration Setup{Colors.NC}")
    print(f"{Colors.BLUE}{'='*44}{Colors.NC}\n")

def _cursor_server_cli_path():
    """Return path to Cursor/Code CLI under ~/.cursor-server (e.g. when using Cursor remote)."""
    system = platform.system()
    if system != "Linux":
        return None
    base = Path.home() / ".cursor-server" / "bin" / "linux-x64"
    if not base.is_dir():
        return None
    for entry in base.iterdir():
        if entry.is_dir():
            cli = entry / "bin" / "remote-cli" / "cursor"
            if cli.is_file() and os.access(cli, os.X_OK):
                return str(cli)
    return None


def _code_server_cli_path():
    """Return path to 'code' CLI under ~/.cursor-server (same server as Cursor)."""
    system = platform.system()
    if system != "Linux":
        return None
    base = Path.home() / ".cursor-server" / "bin" / "linux-x64"
    if not base.is_dir():
        return None
    for entry in base.iterdir():
        if entry.is_dir():
            cli = entry / "bin" / "remote-cli" / "code"
            if cli.is_file() and os.access(cli, os.X_OK):
                return str(cli)
    return None


def _find_ide_command(name, path_candidates):
    """Return first executable path from PATH or path_candidates."""
    cmd = name.lower()
    in_path = shutil.which(cmd)
    if in_path:
        return in_path
    for path in path_candidates:
        if path and Path(path).is_file() and os.access(path, os.X_OK):
            return path
    return None


def _is_remote_environment():
    """Detect if we're running in a remote environment (SSH, container, etc.)"""
    # Check for SSH session
    if os.environ.get('SSH_CLIENT') or os.environ.get('SSH_TTY'):
        return True, "SSH session"
    
    # Check for container environments
    if os.path.exists('/.dockerenv'):
        return True, "Docker container"
    
    if os.environ.get('CONTAINER'):
        return True, "Container environment"
    
    # Check for remote development indicators
    if os.environ.get('REMOTE_CONTAINERS_IPC'):
        return True, "VS Code Remote Containers"
    
    if os.environ.get('CODESPACES'):
        return True, "GitHub Codespaces"
    
    # Check if using cursor-server CLI (remote development)
    cursor_server_path = _cursor_server_cli_path()
    if cursor_server_path:
        return True, "Cursor remote development"
    
    return False, None


def check_extension_installed(ide_command):
    """Check if SonarLint extension is already installed"""
    try:
        verify_result = subprocess.run(
            [ide_command, '--list-extensions'],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if 'sonarsource.sonarlint-vscode' in verify_result.stdout:
            return True
        return False
            
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        print_warning("Unable to check installed extensions")
        return False


def detect_ide():
    """Detect which IDE is installed (Cursor or VS Code)"""
    ide_info = None
    home = Path.home()

    # Cursor: PATH first, then common locations
    cursor_candidates = [
        _cursor_server_cli_path(),
        "/usr/bin/cursor",
        str(home / ".local" / "bin" / "cursor"),
    ]
    cursor_cmd = _find_ide_command("cursor", cursor_candidates)
    if cursor_cmd:
        ide_info = {
            'command': cursor_cmd,
            'name': 'Cursor',
            'settings_path': get_settings_path('Cursor')
        }
        print_success("Detected Cursor IDE")
    # VS Code: PATH first, then common locations
    else:
        code_candidates = [
            _code_server_cli_path(),
            "/usr/bin/code",
            str(home / ".local" / "bin" / "code"),
        ]
        code_cmd = _find_ide_command("code", code_candidates)
        if code_cmd:
            ide_info = {
                'command': code_cmd,
                'name': 'VS Code',
                'settings_path': get_settings_path('Code')
            }
            print_success("Detected VS Code")
        else:
            print_error("Neither Cursor nor VS Code found in PATH or common locations")
            print_info("Please ensure Cursor or VS Code is installed and in your PATH")
            sys.exit(1)

    return ide_info

def get_settings_path(ide_name):
    """Get the settings.json path based on OS and IDE"""
    home = Path.home()
    system = platform.system()

    if system == "Darwin":  # macOS
        return home / "Library" / "Application Support" / ide_name / "User" / "settings.json"
    elif system == "Linux":
        return home / ".config" / ide_name / "User" / "settings.json"
    elif system == "Windows":
        return home / "AppData" / "Roaming" / ide_name / "User" / "settings.json"
    else:
        print_error(f"Unsupported operating system: {system}")
        sys.exit(1)

def prompt_manual_installation():
    """Show manual installation instructions and wait for user confirmation"""
    print("\n" + "="*70)
    print("  📦 MANUAL EXTENSION INSTALLATION REQUIRED")
    print("="*70)
    print()
    print_warning("SonarLint extension must be installed manually via Cursor GUI")
    print_info("This is due to Cursor CLI restrictions in remote/SSH environments")
    print()
    print_info("🎯 INSTALLATION METHODS:")
    print()
    print_info("METHOD 1 - Extensions Marketplace (Recommended):")
    print_info("  1. Press Ctrl+Shift+X to open Extensions panel")
    print_info("  2. Search for 'SonarQube for IDE'")
    print_info("  3. Find extension by SonarSource")
    print_info("  4. Click Install button")
    print_info("  5. Wait for installation to complete")
    print()
    print_info("METHOD 2 - VSIX Download (Alternative):")
    print_info("  1. Visit: https://github.com/SonarSource/sonarlint-vscode/releases/latest")
    print_info("  2. Download the .vsix file from Assets section")
    print_info("  3. In Cursor: Press Ctrl+Shift+X to open Extensions")
    print_info("  4. Click '...' menu → 'Install from VSIX...'")
    print_info("  5. Select the downloaded .vsix file")
    print_info("  6. OR drag-and-drop the .vsix file into Extensions tab")
    print()
    print_info("📖 See README.md for detailed installation instructions")
    print()
    
    input("✋ Press Enter AFTER you've installed the SonarLint extension in Cursor...")

def get_configuration():
    """Get configuration from user input"""
    print(f"\n{Colors.BLUE}Configuration Setup{Colors.NC}")
    print(f"{Colors.YELLOW}Please provide the following information:{Colors.NC}\n")

    # Server URL
    sonar_url = input("SonarQube Server URL (e.g., https://sonar.nvidia.com): ").strip()
    if not sonar_url:
        print_error("Server URL is required")
        sys.exit(1)

    # Token
    sonar_token = input("SonarQube User Token (starts with squ_): ").strip()
    if not sonar_token:
        print_error("User Token is required")
        sys.exit(1)

    if not sonar_token.startswith('squ_'):
        print_warning("Token should start with 'squ_' (User Token), not 'sqp_' (Project Token)")
        confirm = input("Continue anyway? (y/n): ").strip().lower()
        if confirm != 'y':
            sys.exit(1)

    # Project Key
    project_key = input("SonarQube Project Key: ").strip()
    if not project_key:
        print_error("Project Key is required")
        sys.exit(1)

    # Connection ID (consistent with NVIDIA SonarQube setup)
    connection_id = "sonarqube-nvidia"

    # Set default compilation database path for C++ projects
    default_compile_db_path = "${workspaceFolder}/vms_shim/sonar-databases/unified/compile_commands.json"
    
    print_success("Configuration collected")

    return {
        'server_url': sonar_url,
        'token': sonar_token,
        'project_key': project_key,
        'connection_id': connection_id,
        'compile_db_path': default_compile_db_path
    }

def backup_settings(settings_path):
    """Backup existing settings.json"""
    if settings_path.exists():
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = settings_path.with_suffix(f'.json.backup.{timestamp}')
        shutil.copy2(settings_path, backup_path)
        print_success(f"Backed up existing settings to: {backup_path}")
    else:
        print_info("No existing settings file found, will create new one")
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_path, 'w') as f:
            json.dump({}, f)

def update_settings(settings_path, config):
    """Update settings.json with SonarLint configuration"""
    print_info("Updating settings.json...")

    try:
        # Read existing settings
        with open(settings_path, 'r') as f:
            settings = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        settings = {}

    # Add SonarLint configuration
    settings["sonarlint.connectedMode.connections.sonarqube"] = [
        {
            "serverUrl": config['server_url'],
            "connectionId": config['connection_id'],
            "token": config['token']
        }
    ]

    settings["sonarlint.connectedMode.project"] = {
        "connectionId": config['connection_id'],
        "projectKey": config['project_key']
    }

    # Enable auto-analysis on file save
    settings["sonarlint.analyzeOpenFilesOnSave"] = True

    # Write back
    with open(settings_path, 'w') as f:
        json.dump(settings, f, indent=4)

    print_success("Settings.json updated successfully")

def update_workspace_settings(config):
    """Update workspace .vscode/settings.json with compilation database path"""
    # Simple workspace detection - look for .vscode directory or create it
    current_dir = Path.cwd()
    
    # If we're in vms_shim/tools/sonar-lint, go up to find the workspace root
    if "tools/sonar-lint" in str(current_dir):
        # Go up 3 levels: sonar-lint -> tools -> vms_shim -> workspace_root
        workspace_root = current_dir.parent.parent.parent
    elif "vms_shim" in str(current_dir):
        # If we're somewhere in vms_shim, go to parent
        workspace_root = current_dir.parent if current_dir.name == "vms_shim" else current_dir
        # Keep going up until we find a directory that contains vms_shim
        while workspace_root != workspace_root.parent and not (workspace_root / "vms_shim").exists():
            workspace_root = workspace_root.parent
    else:
        # Default to current directory
        workspace_root = current_dir
    
    workspace_settings_path = workspace_root / ".vscode" / "settings.json"
    
    print_info(f"Updating workspace settings: {workspace_settings_path}")
    print_info(f"Detected workspace root: {workspace_root}")
    
    # Create .vscode directory if it doesn't exist
    workspace_settings_path.parent.mkdir(exist_ok=True)
    
    try:
        # Read existing workspace settings
        if workspace_settings_path.exists():
            with open(workspace_settings_path, 'r') as f:
                workspace_settings = json.load(f)
        else:
            workspace_settings = {}
    except (FileNotFoundError, json.JSONDecodeError):
        workspace_settings = {}
    
    # Add/update SonarLint workspace configuration
    workspace_settings["sonarlint.connectedMode.project"] = {
        "connectionId": config['connection_id'],
        "projectKey": config['project_key']
    }
    
    # Set path to compilation database
    workspace_settings["sonarlint.pathToCompileCommands"] = config['compile_db_path']
    
    # Enable analysis of open files
    workspace_settings["sonarlint.analyzeOpenFilesOnSave"] = True
    
    # Write back to workspace settings
    with open(workspace_settings_path, 'w') as f:
        json.dump(workspace_settings, f, indent=4)
    
    print_success("Workspace settings updated successfully")
    print_info(f"Compilation database path set to: {config['compile_db_path']}")

def print_next_steps(ide_name):
    """Print manual steps that user needs to complete"""
    print(f"\n{Colors.YELLOW}⚠️  IMPORTANT: Reload {ide_name} to activate settings:{Colors.NC}")
    print("1. Press Cmd+Shift+P (Mac) or Ctrl+Shift+P (Windows/Linux)")
    print("2. Type: 'Developer: Reload Window'")
    print("3. Press Enter")
    print()
    input("Press Enter when you've reloaded the IDE...")

def print_sync_instructions(ide_name):
    """Print instructions for post-reload setup"""
    print("\n" + "="*60)
    print("  Setup Complete! Follow These Steps to Activate SonarLint")
    print("="*60 + "\n")

    print("IMPORTANT: Complete ALL steps below in " + ide_name + "\n")

    print("Step 1: Reload " + ide_name)
    print("   • Press Cmd+Shift+P (or Ctrl+Shift+P)")
    print("   • Type: 'Developer: Reload Window'")
    print("   • Press Enter and wait for reload\n")

    print("Step 2: Bind to SonarQube (REQUIRED)")
    print("   • Press Cmd+Shift+P (or Ctrl+Shift+P)")
    print("   • Type: 'SonarQube: Bind all workspace folders to SonarQube'")
    print("   • Press Enter and wait for sync (10-20 seconds)\n")

    print("Step 3: Open a C++ file and verify")
    print("   • Open any C++ source file in your project")
    print("   • Issues should appear automatically!")
    print("   • Look for red/yellow squiggles in code")
    print("   • Check status bar: 'SonarLint: X issues'\n")

    print("Troubleshooting:")
    print("   • No issues? Press Cmd+Shift+U → Select 'SonarLint' output")
    print("   • Check connection status in bottom status bar\n")

    print("Full documentation: docs/SONARLINT_IDE_SETUP.md\n")

def main():
    """Main execution function"""
    print_header()

    # Detect IDE
    ide_info = detect_ide()
    
    # Check for remote environment
    is_remote, remote_type = _is_remote_environment()
    if is_remote:
        print_warning(f"Remote environment detected: {remote_type}")
        print_info("Extension installation may require manual steps in remote environments.")
    
    print()

    # Check if extension is already installed
    extension_installed = check_extension_installed(ide_info['command'])
    
    if extension_installed:
        print_success("SonarLint extension already installed")
    else:
        # Always require manual installation for remote environments
        if is_remote:
            print_warning("Due to Cursor CLI restrictions in SSH environments,")
            print_warning("the SonarLint extension must be installed manually.")
            print()
            prompt_manual_installation()
            print()
            
            # Verify after manual installation
            if check_extension_installed(ide_info['command']):
                print_success("✅ SonarLint extension verified as installed!")
                extension_installed = True
            else:
                print_warning("⚠️ Extension not detected. Continuing with configuration anyway...")
                print_info("You may need to reload Cursor after configuration completes.")
        else:
            print_info("SonarLint extension not found. Please install it manually:")
            prompt_manual_installation()
            print()
            if check_extension_installed(ide_info['command']):
                print_success("✅ SonarLint extension verified as installed!")
                extension_installed = True
    
    # Confirm setup
    confirm = input("\nProceed with SonarLint configuration? (y/n): ").strip().lower()
    if confirm != 'y':
        print_info("Setup cancelled")
        sys.exit(0)
    
    print()

    # Get configuration
    config = get_configuration()
    print()

    # Backup and update user settings
    backup_settings(ide_info['settings_path'])
    update_settings(ide_info['settings_path'], config)
    print()
    
    # Update workspace settings
    update_workspace_settings(config)
    print()

    print_success("Setup completed successfully!")
    print()

    print_next_steps(ide_info['name'])
    print_sync_instructions(ide_info['name'])

    print(f"{Colors.GREEN}{'='*44}{Colors.NC}")
    print(f"{Colors.GREEN}  SonarLint Setup Complete!{Colors.NC}")
    print(f"{Colors.GREEN}{'='*44}{Colors.NC}\n")

    print_info("For detailed documentation, see: docs/SONARLINT_IDE_SETUP.md")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Setup cancelled by user{Colors.NC}")
        sys.exit(0)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        sys.exit(1)
