#!/usr/bin/env node

"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const packageRoot = path.resolve(__dirname, "..", "..");
const metadata = require(path.join(packageRoot, "package.json"));
const brandingText = fs.readFileSync(path.join(packageRoot, "src", "goinfre_pm", "project.conf"), "utf8");
function brandingValue(key, fallback) {
  const match = brandingText.match(new RegExp(`^${key}='([^']+)'$`, "m"));
  return match ? match[1] : fallback;
}
const displayName = brandingValue("PROJECT_DISPLAY_NAME", "GoinfrePM");
const commandName = brandingValue("PROJECT_COMMAND", "gpm");
const projectVersion = brandingValue("PROJECT_VERSION", "");
const purple = process.stdout.isTTY && !process.env.NO_COLOR ? "\u001b[38;5;141m" : "";
const red = process.stderr.isTTY && !process.env.NO_COLOR ? "\u001b[31m" : "";
const reset = purple || red ? "\u001b[0m" : "";

function info(message) {
  process.stdout.write(`${purple}[${displayName}]${reset} ${message}\n`);
}

function fail(message, status) {
  process.stderr.write(`${red}[${displayName}] ERROR:${reset} ${message}\n`);
  process.exit(typeof status === "number" ? status : 1);
}

function run(command, args, options) {
  return spawnSync(command, args, Object.assign({ shell: false }, options || {}));
}

function works(command, args) {
  const result = run(command, args, { stdio: "ignore" });
  return !result.error && result.status === 0;
}

function printRequirements() {
  process.stdout.write([
    `${displayName} target: Ubuntu 22.04 (x86_64)`,
    "",
    "Required before running this npx bootstrap:",
    "  node --version",
    "  npm --version",
    "  npx --version",
    "",
    `${displayName} requires the standard Ubuntu Python:`,
    "  python3 --version    # must be 3.10 or newer",
    "",
    "No sudo, system pip, python3-venv, or curl is required. The installer creates",
    "a private environment and bootstraps a pinned, SHA-256-verified pip wheel.",
    "",
    "dpkg-deb is optional and only needed when installing a package distributed as .deb.",
    "If Python or dpkg-deb is missing, ask 1337/42 staff to restore the standard Ubuntu",
    "workstation tools; this bootstrap never runs sudo or apt.",
    "",
  ].join("\n"));
}

function missingRequirements() {
  const missing = [];
  if (!works("python3", ["-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"])) {
    missing.push("Python 3.10 or newer");
  }
  return missing;
}

function installedVersion(launcher) {
  if (!fs.existsSync(launcher)) {
    return null;
  }
  const result = run(launcher, ["version"], { encoding: "utf8" });
  if (result.error || result.status !== 0) {
    return null;
  }
  const match = String(result.stdout || "").trim().match(/(\d+\.\d+\.\d+)/);
  return match ? match[1] : null;
}

function finish(result, action) {
  if (result.error) {
    fail(`Could not ${action}: ${result.error.message}`);
  }
  if (result.signal) {
    fail(`${action} was interrupted by ${result.signal}`, 130);
  }
  if (result.status !== 0) {
    process.exit(typeof result.status === "number" ? result.status : 1);
  }
}

const args = process.argv.slice(2);
if (projectVersion !== metadata.version) {
  fail(`Release metadata is inconsistent (${projectVersion || "missing"} != ${metadata.version}).`);
}
if (args.includes("--requirements")) {
  printRequirements();
  process.exit(0);
}

if (process.platform !== "linux" || process.arch !== "x64") {
  fail("This bootstrap supports x86_64 Ubuntu/Linux only. It intentionally refuses to install on this platform.");
}

const forceIndex = args.indexOf("--reinstall");
const forceInstall = forceIndex !== -1;
if (forceInstall) {
  args.splice(forceIndex, 1);
}

const installer = path.join(packageRoot, "install.sh");
const uninstallIndex = args.indexOf("--uninstall-manager");
if (uninstallIndex !== -1) {
  args.splice(uninstallIndex, 1);
  if (args.length > 0) {
    fail(`--uninstall-manager cannot be combined with ${commandName} command arguments.`);
  }
  finish(run("sh", [installer, "uninstall"], { stdio: "inherit", env: process.env }), `uninstall ${displayName}`);
  process.exit(0);
}

const missing = missingRequirements();
if (missing.length > 0) {
  process.stderr.write(`Missing: ${missing.join(", ")}\n\n`);
  printRequirements();
  fail(`Install the missing prerequisites, then run npx ${metadata.name} again.`);
}

const home = process.env.HOME || os.homedir();
const launcher = path.join(home, ".local", "bin", commandName);
const currentVersion = installedVersion(launcher);

if (forceInstall || currentVersion !== metadata.version) {
  if (!fs.existsSync(installer)) {
    fail("The npm package is incomplete: install.sh is missing.");
  }
  info(currentVersion
    ? `Updating the local manager from ${currentVersion} to ${metadata.version}...`
    : `Installing the local manager ${metadata.version}...`);
  const installResult = run("sh", [installer], { stdio: "inherit", env: process.env });
  finish(installResult, `install ${displayName}`);
} else {
  info(`Local manager ${metadata.version} is already current; skipping installation.`);
}

if (!fs.existsSync(launcher)) {
  fail(`Installation completed without creating ${launcher}.`);
}

finish(run(launcher, args, { stdio: "inherit", env: process.env }), `launch ${commandName}`);
