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
    process.env[key] = String(value);
  }
}

export default async function config(): Promise<NextConfig> {
  await loadAppConfiguration();
  return {};
}
