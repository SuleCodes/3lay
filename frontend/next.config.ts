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
 * config keeps the deployed URL. A deployed build has no .env.local, so it
 * gets App Configuration's values.
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
    // The app is entirely client-side (all data comes from the backend API),
    // so `next build` emits a plain static site into out/, hosted on Azure
    // Static Web Apps. This rules out server-only features -- route
    // handlers, server actions, cookies(), rewrites/redirects, default image
    // optimization; see next/dist/docs/01-app/02-guides/static-exports.md.
    output: "export",
    // Emit /dashboard/index.html rather than /dashboard.html, so every route
    // is a folder any static host serves without rewrite rules.
    trailingSlash: true,
  };
}
