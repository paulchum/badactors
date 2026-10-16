// Vitest's global setup: the tests read the bundled feeds, which are built from
// the engine rather than committed, so export them before any test runs.

import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";

export default function setup(): void {
  execSync("npm run --silent feeds", { cwd: fileURLToPath(new URL(".", import.meta.url)), stdio: "inherit" });
}
