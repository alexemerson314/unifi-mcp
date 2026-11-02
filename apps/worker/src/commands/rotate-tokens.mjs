// src/commands/rotate-tokens.mjs
import prompts from "prompts";
import chalk from "chalk";
import { loadConfig, saveConfig } from "../lib/config.mjs";
import { generateToken } from "../lib/tokens.mjs";
import { ensureWrangler, checkWranglerAuth } from "../lib/prerequisites.mjs";
import { putSecret, login } from "../lib/wrangler.mjs";
import { createLocationToken } from "../lib/api.mjs";
import { showError, showRotatedToken } from "../lib/display.mjs";

export async function run(flags) {
  const config = loadConfig();
  if (!config) {
    showError("No deployment found. Run 'unifi-mcp-worker install' first.");
    process.exit(1);
  }

  let tokenType;

  if (flags["non-interactive"] || flags.token) {
    tokenType = flags.token || "agent";
  } else {
    const answer = await prompts({
      type: "select",
      name: "tokenType",
      message: "Which token to rotate?",
      choices: [
        { title: "Agent token (cloud AI agents)", value: "agent" },
        { title: "Admin token (location management)", value: "admin" },
        { title: "Relay token (sidecar connection)", value: "relay" },
        { title: "All (agent + admin)", value: "all" },
      ],
    });
    tokenType = answer.tokenType;
  }

  try {
    if (tokenType === "agent" || tokenType === "admin" || tokenType === "all") {
      const wranglerOk = await ensureWrangler();
      if (!wranglerOk) process.exit(1);

      const authed = await checkWranglerAuth();
      if (!authed) {
        console.log("You need to log in to Cloudflare.");
        await login();
      }
    }

    if (tokenType === "agent" || tokenType === "all") {
      const newToken = generateToken();
      console.log("Rotating AGENT_TOKEN...");
      await putSecret(config.worker_name, "AGENT_TOKEN", newToken);
      config.agent_token = newToken;
      // The new value is not printed, so persist it before reporting it.
      saveConfig(config);
      showRotatedToken("AGENT_TOKEN", "agent_token", newToken, "Update your AI agent configuration with it.");
    }

    if (tokenType === "admin" || tokenType === "all") {
      const newToken = generateToken();
      console.log("Rotating ADMIN_TOKEN...");
      await putSecret(config.worker_name, "ADMIN_TOKEN", newToken);
      config.admin_token = newToken;
      // The new value is not printed, so persist it before reporting it.
      saveConfig(config);
      showRotatedToken("ADMIN_TOKEN", "admin_token", newToken, "Used for location management.");
    }

    if (tokenType === "relay") {
      if (!config.locations?.length) {
        showError("No locations configured. Run 'unifi-mcp-worker add-location' first.");
        process.exit(1);
      }

      let locationIdx;
      if (config.locations.length === 1) {
        locationIdx = 0;
      } else if (flags["non-interactive"]) {
        const name = flags["location-name"];
        locationIdx = config.locations.findIndex((l) => l.location_name === name);
        if (locationIdx < 0) {
          showError(`Location "${name}" not found.`);
          process.exit(1);
        }
      } else {
        const answer = await prompts({
          type: "select",
          name: "locationIdx",
          message: "Which location's relay token to rotate?",
          choices: config.locations.map((l, i) => ({
            title: l.location_name,
            value: i,
          })),
        });
        locationIdx = answer.locationIdx;
      }

      const location = config.locations[locationIdx];
      console.log(`Rotating relay token for "${location.location_name}"...`);
      const loc = await createLocationToken(
        config.worker_url,
        config.admin_token,
        location.location_name
      );
      config.locations[locationIdx].relay_token = loc.relayToken;
      // The new value is not printed, so persist it before reporting it.
      saveConfig(config);
      showRotatedToken(
        "RELAY_TOKEN",
        `locations[${locationIdx}].relay_token`,
        loc.relayToken,
        "Update your relay sidecar with it."
      );
    }

    console.log(chalk.dim("Config updated."));
  } catch (err) {
    showError(`Token rotation failed: ${err.message}`);
    process.exit(1);
  }
}
