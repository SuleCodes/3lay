import type { NextConfig } from "next";
import { load } from "@azure/app-configuration-provider";
import { DefaultAzureCredential } from "@azure/identity";

// Keys in Azure App Configuration are namespaced per component, e.g.
// "FRONTEND:NEXT_PUBLIC_API_URL". The prefix is trimmed on load.
const APP_CONFIG_PREFIX = "FRONTEND:";

/**
 * All frontend config comes from Azure App Configuration --
 * APP_CONFIG_CONNECTION_STRING (in .env.local, or the host's env) is the only
 * thing that needs setting. Values are loaded into process.env before the app
 * is built, so they follow the normal Next.js rules: NEXT_PUBLIC_* ones get
 * inlined into the client bundle (so the connection string must be available
 * at build time), everything else stays server-only. Deliberately not using
 * next.config's `env` option -- that would ship every value, secrets included,
 * to the browser. Key Vault references resolve via DefaultAzureCredential.
 *
 * Local values win: anything already set -- in .env.local (Next.js loads it
 * before this file runs) or as a real env var -- is kept, and App
 * Configuration only fills in what's missing. So e.g. NEXT_PUBLIC_API_URL in
 * .env.local points a local dev server at a local backend while the shared
 * config keeps the deployed URL. The deployed image is built with
 * NEXT_PUBLIC_API_URL passed in as a build argument (see Dockerfile), and no
 * .env.local or connection string, so App Configuration isn't read there.
 */
async function loadAppConfiguration(): Promise<void> {
  const connectionString = process.env.APP_CONFIG_CONNECTION_STRING;
  if (!connectionString) return;

  const settings = await load(connectionString, {
    selectors: [{ keyFilter: `${APP_CONFIG_PREFIX}*` }],
    trimKeyPrefixes: [APP_CONFIG_PREFIX],
    keyVaultOptions: { credential: new DefaultAzureCredential() },
  });

  for (const [key, value] of settings) {
    if (process.env[key] === undefined || process.env[key] === "") {
      process.env[key] = String(value);
    }
  }
}

export default async function config(): Promise<NextConfig> {
  await loadAppConfiguration();
  return {
    // Runs as a small Node.js server (Azure Container Apps, ca-3lay-web; see
    // Dockerfile), so pages can have dynamic routes like /events/[id]: the
    // address in every webhook's dashboard_url. `standalone` bundles just what
    // the server needs into .next/standalone. Pages still fetch their data in
    // the browser from the API, with its session cookie.
    output: "standalone",
    // `next dev` only: other addresses this machine is opened by (e.g. its LAN
    // IP, to try the app from a phone). localhost is always allowed.
    allowedDevOrigins: ["192.168.100.162"],
    async headers() {
      return [{ source: "/:path*", headers: SECURITY_HEADERS }];
    },
  };
}

// Sent with every page (they were set by Azure Static Web Apps before).
const SECURITY_HEADERS = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Strict-Transport-Security", value: "max-age=31536000" },
];
