#!/usr/bin/env node
// Keep vinext's stdin open when the dashboard is launched by launchd.

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const child = spawn(
  process.execPath,
  [
    "node_modules/vinext/dist/cli.js",
    "dev",
    "--hostname",
    "127.0.0.1",
    "--port",
    "3000",
  ],
  {
    cwd: root,
    stdio: ["pipe", "inherit", "inherit"],
  },
);

let stopping = false;
function stop(signal) {
  if (stopping) return;
  stopping = true;
  child.kill(signal);
  setTimeout(() => child.kill("SIGKILL"), 10_000).unref();
}

process.on("SIGINT", () => stop("SIGINT"));
process.on("SIGTERM", () => stop("SIGTERM"));

child.on("error", (error) => {
  console.error("Unable to start the dashboard:", error);
  process.exitCode = 1;
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});
