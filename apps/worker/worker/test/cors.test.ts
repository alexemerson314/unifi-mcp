// test/cors.test.ts
import { describe, it, expect } from "vitest";
import { corsHeaders, parseAllowedOrigins } from "../src/cors";
import type { Env } from "../src/types";

function env(allowed?: string): Env {
  return { CORS_ALLOWED_ORIGINS: allowed } as Env;
}

function withOrigin(origin?: string): Request {
  return new Request("https://relay.example/mcp", { headers: origin ? { Origin: origin } : {} });
}

describe("parseAllowedOrigins", () => {
  it("normalizes entries to serialized origins and skips invalid ones", () => {
    expect(parseAllowedOrigins(" https://app.example/ ,http://localhost:5173,not a url,, file:///x")).toEqual(
      new Set(["https://app.example", "http://localhost:5173"]),
    );
  });

  it("is empty when unset", () => {
    expect(parseAllowedOrigins(undefined)).toEqual(new Set());
  });
});

describe("corsHeaders", () => {
  it("echoes an allowlisted Origin", () => {
    const headers = corsHeaders(withOrigin("https://app.example"), env("https://app.example"));

    expect(headers["Access-Control-Allow-Origin"]).toBe("https://app.example");
    expect(headers["Access-Control-Allow-Headers"]).toContain("Authorization");
    expect(headers.Vary).toBe("Origin");
  });

  it("allows the Streamable HTTP session and protocol headers and exposes the session ID", () => {
    const headers = corsHeaders(withOrigin("https://app.example"), env("https://app.example"));

    expect(headers["Access-Control-Allow-Headers"].split(", ")).toEqual(
      expect.arrayContaining(["Mcp-Session-Id", "MCP-Protocol-Version"]),
    );
    expect(headers["Access-Control-Expose-Headers"]).toBe("Mcp-Session-Id");
  });

  it.each([
    ["unset", undefined],
    ["empty", ""],
    ["blank entries only", " , "],
  ])("echoes any Origin when the list is %s", (_label, allowed) => {
    const headers = corsHeaders(withOrigin("https://anywhere.example"), env(allowed));

    expect(headers["Access-Control-Allow-Origin"]).toBe("https://anywhere.example");
    expect(headers.Vary).toBe("Origin");
    expect(headers).not.toHaveProperty("Access-Control-Allow-Credentials");
  });

  it.each([
    ["an Origin not on the list", "https://evil.example", "https://app.example"],
    ["any Origin when the list names only invalid entries", "https://app.example", "not a url"],
    ["a different port", "https://app.example:8443", "https://app.example"],
    ["the opaque null Origin", "null", "https://app.example"],
  ])("sends no Access-Control-Allow-* header for %s", (_label, origin, allowed) => {
    const headers = corsHeaders(withOrigin(origin), env(allowed));

    expect(Object.keys(headers)).toEqual(["Vary"]);
  });

  it.each([
    ["a configured list", "https://app.example"],
    ["an unset list", undefined],
  ])("sends no Access-Control-Allow-* header without an Origin under %s", (_label, allowed) => {
    expect(corsHeaders(withOrigin(), env(allowed))).toEqual({ Vary: "Origin" });
  });
});
