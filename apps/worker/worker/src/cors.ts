// src/cors.ts
import type { Env } from "./types";

/** Parse CORS_ALLOWED_ORIGINS into serialized origins, skipping invalid entries */
export function parseAllowedOrigins(value: string | undefined): Set<string> {
  const origins = new Set<string>();
  for (const entry of (value ?? "").split(",")) {
    const trimmed = entry.trim();
    if (!trimmed) continue;
    try {
      const origin = new URL(trimmed).origin;
      if (origin !== "null") origins.add(origin);
    } catch {
      // not an origin; ignore
    }
  }
  return origins;
}

/** Whether CORS_ALLOWED_ORIGINS names any entry, valid or not */
function allowlistConfigured(value: string | undefined): boolean {
  return (value ?? "").split(",").some((entry) => entry.trim() !== "");
}

/**
 * CORS headers for a request. With CORS_ALLOWED_ORIGINS unset or empty the
 * request's Origin is echoed. Once it names any entry, the Origin is echoed
 * only when it is on the list; otherwise no Access-Control-Allow-* header is
 * sent and browsers refuse the cross-origin read. A missing Origin and the
 * opaque "null" Origin never match a list.
 */
export function corsHeaders(request: Request, env: Env): Record<string, string> {
  const headers: Record<string, string> = { Vary: "Origin" };
  const origin = request.headers.get("Origin");
  if (!origin) return headers;
  const allowed =
    !allowlistConfigured(env.CORS_ALLOWED_ORIGINS) || parseAllowedOrigins(env.CORS_ALLOWED_ORIGINS).has(origin);
  if (allowed) {
    headers["Access-Control-Allow-Origin"] = origin;
    headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS";
    headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, Mcp-Session-Id, MCP-Protocol-Version";
    headers["Access-Control-Expose-Headers"] = "Mcp-Session-Id";
  }
  return headers;
}
