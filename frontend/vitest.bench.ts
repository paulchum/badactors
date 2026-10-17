// Vitest's global setup: the standings tests read the bot cast's bench files,
// which are built rather than committed, so export them when they are missing
// or from another version of the export.

import { execSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const VERSION = 1;

function current(): boolean {
  const seats = new URL("./public/bench/seats.json", import.meta.url);
  if (!existsSync(seats)) return false;
  try {
    return (JSON.parse(readFileSync(seats, "utf8")) as { version?: number }).version === VERSION;
  } catch {
    return false;
  }
}

export default function setup(): void {
  if (current()) return;
  execSync("npm run --silent bench", { cwd: fileURLToPath(new URL(".", import.meta.url)), stdio: "inherit" });
}
