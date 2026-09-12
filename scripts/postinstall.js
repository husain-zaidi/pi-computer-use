#!/usr/bin/env node
/**
 * postinstall — one-time Python setup for the pi computer-use worker.
 *
 * pi runs `npm install` when it installs a package from npm or git, so this
 * hook is where the JS side hands off to the Python side. It:
 *   1. creates `.venv` in the package directory (where extensions/index.ts resolves),
 *   2. pip-installs requirements.txt into it, and
 *   3. downloads Playwright's Chromium (shared browser cache).
 *
 * Best-effort by design: if Python or the network aren't available, it prints
 * the manual steps and exits 0 so `pi install` still succeeds — the worker just
 * isn't ready until setup completes. Set PI_CUA_SKIP_POSTINSTALL=1 to skip.
 */

import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

const root = process.cwd();
const isWin = process.platform === "win32";
const venvDir = path.join(root, ".venv");
const venvPython = isWin ? path.join(venvDir, "Scripts", "python.exe") : path.join(venvDir, "bin", "python");

function findBasePython() {
  const candidates = isWin ? ["python", "py"] : ["python3", "python"];
  for (const cmd of candidates) {
    try {
      execFileSync(cmd, ["-V"], { stdio: "ignore" });
      return cmd;
    } catch {}
  }
  return null;
}

function run(cmd, args) {
  execFileSync(cmd, args, { stdio: "inherit" });
}

function skipManualSteps() {
  console.log("[pi-computer-use] To finish the one-time Python setup manually, run once in this directory:");
  if (isWin) {
    console.log("    python -m venv .venv && .\\..\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt && .\\..\\.venv\\Scripts\\python.exe -m playwright install chromium");
  } else {
    console.log("    python3 -m venv .venv && ./.venv/bin/python -m pip install -r requirements.txt && ./.venv/bin/python -m playwright install chromium");
  }
}

if (process.env.PI_CUA_SKIP_POSTINSTALL) {
  console.log("[pi-computer-use] postinstall skipped (PI_CUA_SKIP_POSTINSTALL set).");
  process.exit(0);
}

const base = findBasePython();
if (!base) {
  console.log("[pi-computer-use] postinstall: no Python found; skipping auto setup.");
  skipManualSteps();
  process.exit(0);
}

try {
  if (!existsSync(venvPython)) {
    console.log("[pi-computer-use] creating .venv");
    run(base, ["-m", "venv", ".venv"]);
  }

  console.log("[pi-computer-use] installing Python requirements into .venv");
  run(venvPython, ["-m", "pip", "install", "-r", "requirements.txt"]);

  console.log("[pi-computer-use] downloading Playwright Chromium (idempotent)");
  run(venvPython, ["-m", "playwright", "install", "chromium"]);

  console.log("[pi-computer-use] setup complete.");
} catch (err) {
  console.error("[pi-computer-use] postinstall auto setup failed:", err.message);
  skipManualSteps();
  process.exit(0);
}
