// Production build for deployment: `npm run build:deploy`.
//
// NEXT_PUBLIC_API_URL is baked into the static files at build time. A local
// override in .env.local (e.g. http://localhost:8000) must never end up in
// the deployed site, so this build blanks it -- next.config.ts then fills it
// from App Configuration (FRONTEND:NEXT_PUBLIC_API_URL). Afterwards it checks
// the output really points at a deployed HTTPS API before anything can be
// uploaded.

import { spawnSync } from "node:child_process";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const OUT_DIR = "out";

// `--check-only` skips the build and just checks the existing out/ folder.
if (!process.argv.includes("--check-only")) {
  // One command string (not an args array) because Windows needs a shell to
  // run npx.
  const result = spawnSync("npx next build", {
    stdio: "inherit",
    shell: true,
    // Empty (rather than unset) so Next.js doesn't load the .env.local value;
    // next.config.ts treats empty as "not set locally" and uses App Configuration.
    env: { ...process.env, NEXT_PUBLIC_API_URL: "" },
  });
  if (result.status !== 0) process.exit(result.status ?? 1);
}

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* files(path);
    else if (/\.(js|html)$/.test(name)) yield path;
  }
}

const bundle = [...files(OUT_DIR)].map((f) => readFileSync(f, "utf8")).join("\n");
const problems = [];
if (/localhost:\d+/.test(bundle)) problems.push("the build references localhost");
if (!/https:\/\/[a-z0-9.-]+/i.test(bundle)) problems.push("no HTTPS API URL found in the build");
// The session cookie only works when the API shares a site with the frontend
// (api.3lay.live with app.3lay.live). Azure's default hostnames are separate
// sites, so sign-in would silently loop back to the login page.
const defaultHost = bundle.match(/https:\/\/[a-z0-9.-]+\.(azurecontainerapps\.io|azurewebsites\.net|azurestaticapps\.net)/i);
if (defaultHost) {
  problems.push(
    `the API URL is an Azure default address (${defaultHost[0]}); use the custom domain, e.g. https://api.3lay.live, or sign-in won't work`,
  );
}

if (problems.length) {
  console.error(`\nbuild:deploy refused: ${problems.join("; ")}.`);
  console.error("Check FRONTEND:NEXT_PUBLIC_API_URL in App Configuration and APP_CONFIG_CONNECTION_STRING.");
  process.exit(1);
}
console.log(`\nbuild:deploy OK: static site in ./${OUT_DIR}, ready to deploy with \`npm run deploy\`.`);
