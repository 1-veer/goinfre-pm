"use strict";

// Exercise the published entry point's branch decisions on any test platform.
// All child processes are mocked: no package, shell file, or user home changes.
const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");

const root = path.resolve(process.argv[2]);
const requested = JSON.parse(process.argv[3]);
const source = fs.readFileSync(path.join(root, "npm", "bin", "goinfre-pm.js"), "utf8");
const home = "/test-only-home";
const launcher = path.join(home, ".local", "bin", "gpm");
const calls = [];
let exitCode = 0;

const fakeFs = new Proxy(fs, {
  get(target, property) {
    if (property === "existsSync") {
      return (filename) => filename === launcher ? true : target.existsSync(filename);
    }
    return target[property];
  },
});
const fakeChild = {
  spawnSync(command, args) {
    calls.push({ command, args });
    if (command === launcher && args[0] === "version") {
      return { status: 0, stdout: "GoinfrePM 1.5.2\n" };
    }
    return { status: 0, stdout: "" };
  },
};
const fakeProcess = {
  argv: ["node", path.join(root, "npm", "bin", "goinfre-pm.js"), ...requested],
  platform: "linux",
  arch: "x64",
  env: { HOME: home },
  stdout: { isTTY: false, write() {} },
  stderr: { isTTY: false, write() {} },
  exit(code) { throw { exitCode: code }; },
};
const fakeRequire = (name) => {
  if (name === "fs") return fakeFs;
  if (name === "os") return os;
  if (name === "path") return path;
  if (name === "child_process") return fakeChild;
  return require(name);
};

try {
  vm.runInNewContext(source, {
    require: fakeRequire,
    __dirname: path.join(root, "npm", "bin"),
    process: fakeProcess,
  });
} catch (error) {
  if (!error || typeof error.exitCode !== "number") throw error;
  exitCode = error.exitCode;
}

process.stdout.write(JSON.stringify({ calls, exitCode }));
