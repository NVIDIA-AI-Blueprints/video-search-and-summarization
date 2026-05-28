// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { homedir } from "node:os";
import { execSync } from "node:child_process";

type Logger = { info: (msg: string) => void; warn: (msg: string) => void };

export default function register(api: {
  config: { agents?: { defaults?: { workspace?: string } } };
  source: string;
  logger: Logger;
}) {
  copyWorkspaceTemplates(api);
  patchGatewayDockerGroup(api);
}

function resolveVariant(pluginDir: string, logger: Logger): string | undefined {
  // OPENCLAW_PLUGIN_VARIANT is only set on the install command, not in the
  // gateway service env, so persist it next to the plugin at install time
  // and prefer the persisted value on subsequent register() calls.
  const variantFile = join(pluginDir, ".variant");
  const envVariant = process.env.OPENCLAW_PLUGIN_VARIANT?.trim();
  if (envVariant) {
    try {
      writeFileSync(variantFile, envVariant, "utf8");
    } catch (err) {
      logger.warn(`[vss-claw] failed to persist variant: ${err}`);
    }
    return envVariant;
  }
  if (!existsSync(variantFile)) return undefined;
  try {
    return readFileSync(variantFile, "utf8").trim() || undefined;
  } catch (err) {
    logger.warn(`[vss-claw] failed to read persisted variant: ${err}`);
    return undefined;
  }
}

function copyWorkspaceTemplates(api: {
  config: { agents?: { defaults?: { workspace?: string } } };
  source: string;
  logger: Logger;
}) {
  const workspaceDir = api.config?.agents?.defaults?.workspace;
  if (!workspaceDir) return;

  const pluginDir = dirname(api.source);
  const templatesDir = join(pluginDir, "workspace");
  const variant = resolveVariant(pluginDir, api.logger);

  try {
    mkdirSync(workspaceDir, { recursive: true });
    const files = readdirSync(templatesDir).filter((f) => f.endsWith(".md"));
    for (const file of files) {
      copyFileSync(join(templatesDir, file), join(workspaceDir, file));
    }
    api.logger.info(`[vss-claw] copied ${files.length} workspace templates to ${workspaceDir}`);

    if (variant) {
      const overlayDir = join(templatesDir, `_${variant}`);
      if (existsSync(overlayDir)) {
        const overlayFiles = readdirSync(overlayDir).filter((f) => f.endsWith(".md"));
        for (const file of overlayFiles) {
          copyFileSync(join(overlayDir, file), join(workspaceDir, file));
        }
        api.logger.info(`[vss-claw] applied _${variant} overrides (${overlayFiles.length} files)`);
      } else {
        api.logger.warn(`[vss-claw] variant='${variant}' set but ${overlayDir} is missing`);
      }
    }
  } catch (err) {
    api.logger.warn(`[vss-claw] workspace copy failed: ${err}`);
  }
}

function patchGatewayDockerGroup(api: {
  logger: Logger;
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
