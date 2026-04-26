import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { homedir } from "node:os";
import { execSync } from "node:child_process";

export default function register(api: {
  config: { agents?: { defaults?: { workspace?: string } } };
  source: string;
  logger: { info: (msg: string) => void; warn: (msg: string) => void };
}) {
  copyWorkspaceTemplates(api);
  patchGatewayDockerGroup(api);
  installAgentBrowserSkill(api);
}

function copyWorkspaceTemplates(api: {
  config: { agents?: { defaults?: { workspace?: string } } };
  source: string;
  logger: { info: (msg: string) => void; warn: (msg: string) => void };
}) {
  const workspaceDir = api.config?.agents?.defaults?.workspace;
  if (!workspaceDir) return;

  const templatesDir = join(dirname(api.source), "workspace");

  try {
    mkdirSync(workspaceDir, { recursive: true });
    const files = readdirSync(templatesDir).filter((f) => f.endsWith(".md"));
    for (const file of files) {
      copyFileSync(join(templatesDir, file), join(workspaceDir, file));
    }
    api.logger.info(`[vss-claw] copied ${files.length} workspace templates to ${workspaceDir}`);
  } catch (err) {
    api.logger.warn(`[vss-claw] workspace copy failed: ${err}`);
  }
}

function installAgentBrowserSkill(api: {
  config: { agents?: { defaults?: { workspace?: string } } };
  logger: { info: (msg: string) => void; warn: (msg: string) => void };
}) {
  const workspaceDir = api.config?.agents?.defaults?.workspace;
  if (!workspaceDir) return;

  const skillDir = join(workspaceDir, "skills", "agent-browser");
  if (existsSync(skillDir)) return;

  try {
    mkdirSync(join(workspaceDir, "skills"), { recursive: true });
    execSync("npx --yes skills add vercel-labs/agent-browser --yes", {
      cwd: workspaceDir,
      stdio: "ignore",
    });
    api.logger.info("[vss-claw] installed agent-browser skill into workspace");
  } catch (err) {
    api.logger.warn(`[vss-claw] agent-browser skill install failed: ${err}`);
  }
}

function patchGatewayDockerGroup(api: {
  logger: { info: (msg: string) => void; warn: (msg: string) => void };
}) {
  // Only patch if docker socket exists
  if (!existsSync("/var/run/docker.sock")) return;

  const serviceFile = join(homedir(), ".config/systemd/user/openclaw-gateway.service");
  if (!existsSync(serviceFile)) return;

  const dropinDir = join(homedir(), ".config/systemd/user/openclaw-gateway.service.d");
  const dropinFile = join(dropinDir, "10-docker.conf");

  try {
    const content = readFileSync(serviceFile, "utf8");

    // Extract the ExecStart from the main service file
    const match = content.match(/^ExecStart=(.+)$/m);
    if (!match) return;
    const execStart = match[1];

    // If the main file already has sg docker (manual patch), nothing to do
    if (execStart.includes("sg docker")) return;

    // Check if drop-in already wraps this exact ExecStart
    if (existsSync(dropinFile)) {
      const dropinContent = readFileSync(dropinFile, "utf8");
      if (dropinContent.includes(execStart)) return;
    }

    // Create/update drop-in — clears original ExecStart and sets wrapped version
    mkdirSync(dropinDir, { recursive: true });
    writeFileSync(
      dropinFile,
      [
        "# Added by vss-claw plugin — wraps ExecStart with sg docker for Docker socket access",
        "[Service]",
        "ExecStart=",
        `ExecStart=/bin/sg docker -c '${execStart}'`,
      ].join("\n") + "\n",
      "utf8"
    );

    execSync("systemctl --user daemon-reload", { stdio: "ignore" });
    api.logger.info("[vss-claw] created docker drop-in for openclaw-gateway — restart the gateway to apply");
  } catch (err) {
    api.logger.warn(`[vss-claw] docker group patch failed: ${err}`);
  }
}
