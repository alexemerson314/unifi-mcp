import { describe, it, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("showRotatedToken", () => {
  let tmpDir;
  let display;

  before(async () => {
    tmpDir = mkdtempSync(join(tmpdir(), "unifi-mcp-worker-test-"));
    process.env.UNIFI_MCP_WORKER_CONFIG_DIR = tmpDir;
    display = await import(`../../src/lib/display.mjs?dir=${encodeURIComponent(tmpDir)}`);
  });

  after(() => {
    rmSync(tmpDir, { recursive: true, force: true });
    delete process.env.UNIFI_MCP_WORKER_CONFIG_DIR;
  });

  it("prints the masked token and where it was saved, never the token", (t) => {
    const lines = [];
    t.mock.method(console, "log", (...args) => lines.push(args.join(" ")));
    const token = "1b4e28ba-2fa1-11d2-883f-0016d3cca427";

    display.showRotatedToken("AGENT_TOKEN", "agent_token", token, "Update your agent.");

    const output = lines.join("\n");
    assert.ok(!output.includes(token), "rotated token must not reach stdout");
    assert.ok(output.includes("****-a427"));
    assert.ok(output.includes("agent_token"));
    assert.ok(output.includes(join(tmpDir, "config.json")));
  });
});
