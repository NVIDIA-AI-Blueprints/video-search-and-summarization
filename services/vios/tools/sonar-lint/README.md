# SonarLint Setup - Simple 3-Step Guide

Complete SonarLint setup for C++ code analysis in 3 simple steps. Generate the compilation database first, then let the automated installer configure everything for you.

## ⚙️ Step 1: Generate Compilation Database

**Important: Run from project root** (where your Makefile is located):

```bash
cd /path/to/vms_shim  # Navigate to project root

# Generate compilation database
source ./tools/sonar-lint/sonar-lint.env && ./tools/sonar-lint/generate_compile_command.sh x86 --unified --force
```

**What gets created:**

- `vms_shim/sonar-databases/x86/compile_commands.json` - x86-specific database
- `vms_shim/sonar-databases/unified/compile_commands.json` - Combined database for SonarQube

**Build options:**

- `x86` - x86 build only (recommended for faster analysis)
- `--all` - All platforms (x86, cc1, cc2)
- `--unified` - Creates unified database for SonarQube

## 🚀 Step 2: Install SonarLint Extension

### 📦 Manual Extension Installation (Required for Remote Environments)

**Due to Cursor CLI restrictions in SSH/remote environments, the extension must be installed manually via the GUI:**

**METHOD 1 - Extensions Marketplace (Recommended):**

1. Press `Ctrl+Shift+X` to open Extensions panel
2. Search for `SonarQube for IDE`
3. Click Install on extension by SonarSource
4. Wait for installation to complete

**METHOD 2 - VSIX Download (Alternative for Cursor):**

1. Visit: [https://github.com/SonarSource/sonarlint-vscode/releases/latest](https://github.com/SonarSource/sonarlint-vscode/releases/latest)
2. Download the `.vsix` file from Assets section
3. In Cursor: Press `Ctrl+Shift+X` to open Extensions
4. Click `...` menu → `Install from VSIX...`
5. Select the downloaded `.vsix` file
6. OR simply drag-and-drop the `.vsix` file into Extensions tab

### ⚙️ Configure SonarLint Settings

After installing the extension manually, run the configuration script:

```bash
cd /path/to/vms_shim  # Navigate to project root
python3 ./tools/sonar-lint/sonar-lint-config.py
```

**The script will automatically:**

- Configure both user and workspace settings  
- Set the compilation database path to: `${workspaceFolder}/vms_shim/sonar-databases/unified/compile_commands.json`
- Connect to your SonarQube server

**You'll need to provide:**

- SonarQube Server URL (e.g., `https://sonar.nvidia.com`)
- Your SonarQube User Token (starts with `squ_`)
- Project Key from SonarQube (e.g., `TEGRASW_VST_VST_vms_shim`)

**Steps to generate token:**

1. Log in to SonarQube
2. Go to My Account → Security → Tokens
3. Generate a User Token (do not generate a Project Token)

## ✅ Step 3: Verify SonarLint Issues

1. **Reload IDE:**
  - Press `Ctrl+Shift+P` (or `Cmd+Shift+P` on Mac)
  - Type: `Developer: Reload Window` → Press Enter
2. **Bind to SonarQube** (CRITICAL):
  - Press `Ctrl+Shift+P` (or `Cmd+Shift+P` on Mac)
  - Type: `SonarQube: Bind all workspace folders to SonarQube`
  - Press Enter and wait 10-20 seconds
3. **Open a C++ file:**
  - Open any `.cpp` or `.h` file in your project
  - Look for red/yellow squiggles indicating issues
  - Check status bar: Should show "SonarLint: X issues"

---

## 🤖 Step 4: Setup SonarQube MCP Server (Optional)

For enhanced AI-powered code analysis and SonarQube integration in Cursor, you can set up the SonarQube MCP server:

**📖 Installation Guide (One Click):**  
Visit: [https://ipp-safety-tools.gitlab-master-pages.nvidia.com/giza-llm-tools/giza_ai/docs/2.3.0/tutorial/quickstart](https://ipp-safety-tools.gitlab-master-pages.nvidia.com/giza-llm-tools/giza_ai/docs/2.3.0/tutorial/quickstart)

Search for SonarQube and click on 'Add to Cursor'

**📚 Examples and Usage:**  
Visit: [https://ipp-safety-tools.gitlab-master-pages.nvidia.com/giza-llm-tools/giza_ai/docs/preprod/category/sonarqube](https://ipp-safety-tools.gitlab-master-pages.nvidia.com/giza-llm-tools/giza_ai/docs/preprod/category/sonarqube)

**What the MCP Server provides:**

- AI assistant integration with SonarQube APIs
- Query metrics, issues, and quality gates through AI chat
- Enhanced code analysis and project insights
- Integration with your existing SonarQube setup

**Setup Steps:**

1. Follow the guide at the link above to configure the MCP server
2. The MCP server will use the same SonarQube connection details:
  - Server URL: `https://sonar.nvidia.com`
  - User Token: Same token used in Step 2
  - Project Key: `TEGRASW_VST_VST_vms_shim`

**Benefits:**

- Ask AI questions about your code quality metrics
- Get insights on SonarQube issues directly in Cursor
- Analyze project health through natural language queries

---

## 🔧 Troubleshooting

### No Issues Showing?

- **Complete the "Bind to SonarQube" step above** (most common issue)
- Check SonarLint output: `Ctrl+Shift+U` → Select 'SonarLint'
- Verify your token and project key are correct
- **Check if settings were configured properly:**
  - User settings: `~/.config/Cursor/User/settings.json` (should contain SonarQube connection)
  - Workspace settings: `.vscode/settings.json` (should contain project configuration and compilation database path)

### Check Compilation Database

```bash
# Verify database exists and has content
ls -la ./sonar-databases/unified/compile_commands.json
jq length ./sonar-databases/unified/compile_commands.json

# Check paths are correct (should NOT contain /root/)
jq -r '.[0].file' ./sonar-databases/unified/compile_commands.json
```

### Docker Image Issues

If you see "docker: invalid reference format" or auto-detected images show "/root":

```bash
# This means Docker images aren't set properly
# Solution: Source the environment file first
source ./tools/sonar-lint/sonar-lint.env

# Then run the script
./tools/sonar-lint/generate_compile_command.sh x86 --unified
```

### Fix Container Paths

If paths contain `/root/` instead of your project path:

```bash
./tools/sonar-lint/generate_compile_command.sh --fix-paths
```

### Docker Permission Issues

```bash
sudo usermod -a -G docker $USER
newgrp docker
```

### Check Overall Status

```bash
./tools/sonar-lint/generate_compile_command.sh --status
```

---

## ✅ Success Indicators

You know it's working when you see:

- ✅ SonarLint extension installed and configured
- ✅ User settings (`~/.config/Cursor/User/settings.json`) updated with SonarQube connection
- ✅ Workspace settings (`.vscode/settings.json`) updated with project configuration
- ✅ `./sonar-databases/unified/compile_commands.json` exists with correct paths
- ✅ Red/yellow squiggles appear in C++ source files
- ✅ Status bar shows "SonarLint: X issues"
- ✅ SonarLint output panel shows analysis results

---

## 📁 Final Directory Structure

```
your-project/
├── vms_shim/
│   ├── tools/
│   │   └── sonar-lint/
│   │       ├── generate_compile_command.sh
│   │       ├── sonar-lint-config.py
│   │       ├── sonar-lint.env
│   │       └── README.md
│   └── sonar-databases/
│       ├── x86/compile_commands.json
│       ├── unified/compile_commands.json    # ← Main database for SonarQube
│       └── backups/
├── .vscode/
│   └── settings.json                       # Auto-configured workspace settings
├── Makefile                               # Your project Makefile (required)
└── src/                                  # Your source code
```

---

## 🆘 Quick Command Reference

```bash
# Step 1: Generate compilation database (from project root) 
source ./tools/sonar-lint/sonar-lint.env && ./tools/sonar-lint/generate_compile_command.sh x86 --unified --force

# Step 2: Configure SonarLint settings (from project root)
python3 ./tools/sonar-lint/sonar-lint-config.py

# Fix container paths if needed  
source ./tools/sonar-lint/sonar-lint.env && ./tools/sonar-lint/generate_compile_command.sh --fix-paths

# Check status
source ./tools/sonar-lint/sonar-lint.env && ./tools/sonar-lint/generate_compile_command.sh --status

# Environment setup (REQUIRED for proper Docker images)
source ./tools/sonar-lint/sonar-lint.env
```

That's it! Your SonarLint should now be working with C++ code analysis.