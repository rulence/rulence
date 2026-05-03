#!/usr/bin/env node
import { execFile, spawn, spawnSync } from "node:child_process";
import { dirname, delimiter, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const PYTHON = process.env.RULENCE_PYTHON || process.env.PYTHON || "python3";

export function classifyTask(task) {
  return callRulenceSync(["classify", task, "--json"]);
}

export function preflightTask(task, options = {}) {
  return callRulenceSync(["preflight", task, ...memoryArgs(options), ...policyArgs(options), "--json"], [0, 2]);
}

export function decomposePrompt(prompt, options = {}) {
  const args = ["decompose", prompt, "--json"];
  if (options.max_depth) args.splice(2, 0, "--max-depth", String(options.max_depth));
  return callRulenceSync(args);
}

export function sequentialThinking(input) {
  return callRulenceSync(["sequentialthinking", "--input-json", JSON.stringify(input), "--json"]);
}

export async function runServer() {
  const child = spawn(PYTHON, ["-m", "rulence", "mcp"], {
    cwd: ROOT,
    env: pythonEnv(),
    stdio: ["pipe", "pipe", "inherit"],
  });

  process.stdin.pipe(child.stdin);
  child.stdout.pipe(process.stdout);
  process.stdin.on("end", () => child.stdin.end());

  const forward = (signal) => {
    if (!child.killed) child.kill(signal);
  };
  process.once("SIGINT", () => forward("SIGINT"));
  process.once("SIGTERM", () => forward("SIGTERM"));

  return new Promise((resolve) => {
    child.on("exit", (code, signal) => {
      process.exitCode = code ?? (signal ? 1 : 0);
      resolve();
    });
  });
}

export async function callRulence(args, allowedCodes = [0]) {
  try {
    const { stdout } = await execFileAsync(PYTHON, ["-m", "rulence", ...args], {
      cwd: ROOT,
      env: pythonEnv(),
      maxBuffer: 10 * 1024 * 1024,
    });
    return parseJson(stdout);
  } catch (error) {
    if (allowedCodes.includes(error.code) && error.stdout) {
      return parseJson(error.stdout);
    }
    const detail = String(error.stderr || error.message || error);
    throw new Error(detail.trim());
  }
}

export function callRulenceSync(args, allowedCodes = [0]) {
  const child = spawnSync(PYTHON, ["-m", "rulence", ...args], {
    cwd: ROOT,
    env: pythonEnv(),
    encoding: "utf8",
    maxBuffer: 10 * 1024 * 1024,
  });
  if (!allowedCodes.includes(child.status ?? 1)) {
    throw new Error((child.stderr || child.error?.message || "rulence command failed").trim());
  }
  return parseJson(child.stdout);
}

function memoryArgs(args) {
  const output = [];
  if (args.memory) output.push("--memory", args.memory);
  if (args.memory_provider) output.push("--memory-provider", args.memory_provider);
  if (args.memory_transport) output.push("--memory-transport", args.memory_transport);
  if (args.memory_path) output.push("--memory-path", args.memory_path);
  if (args.memory_url) output.push("--memory-url", args.memory_url);
  if (args.memory_limit) output.push("--memory-limit", String(args.memory_limit));
  return output;
}

function policyArgs(args) {
  return args.policy_dir ? ["--policy-dir", args.policy_dir] : [];
}

function pythonEnv() {
  const src = join(ROOT, "src");
  return {
    ...process.env,
    PYTHONPATH: [src, process.env.PYTHONPATH].filter(Boolean).join(delimiter),
  };
}

function parseJson(stdout) {
  try {
    return JSON.parse(stdout);
  } catch {
    throw new Error(`rulence returned non-JSON output: ${stdout.slice(0, 500)}`);
  }
}

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1];
if (isMain) {
  runServer().catch((error) => {
    console.error("Fatal error running Rulence MCP server:", error);
    process.exit(1);
  });
}
