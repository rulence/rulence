import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const sessionDir = mkdtempSync(join(tmpdir(), "rulence-js-test-"));
process.env.RULENCE_SESSION_DIR = sessionDir;
process.env.DISABLE_THOUGHT_LOGGING = "true";

const { callRulence, classifyTask, decomposePrompt, preflightTask, sequentialThinking } = await import("../src-js/mcp-server.js");

test("classifies destructive production task as high risk", () => {
  const result = classifyTask("delete production credentials");
  assert.equal(result.tier, 5);
  assert.equal(result.label, "Tier 5 / high-risk");
});

test("preflight blocks high-risk action", () => {
  const result = preflightTask("delete production credentials");
  assert.equal(result.verdict, "block");
  assert.ok(result.blocks.includes("sensitive_or_destructive_action"));
});

test("decompose returns a typed task DAG", () => {
  const result = decomposePrompt("Build API. Then build UI.");
  assert.equal(result.root_units.length, 2);
  assert.deepEqual(result.root_units[1].depends_on, [result.root_units[0].id]);
});

test("classifier uses word boundaries instead of substring matches", () => {
  const result = classifyTask("fix syntax in the rapid parser");
  assert.equal(result.signals.high_risk_terms.includes("tax"), false);
  assert.equal(result.signals.tool_terms.includes("api"), false);
});

test("sequentialthinking returns compatibility shape with rulence metadata", () => {
  const result = sequentialThinking({
    thought: "Start with the riskiest assumption.",
    nextThoughtNeeded: true,
    thoughtNumber: 1,
    totalThoughts: 3,
  });
  assert.equal(result.thoughtNumber, 1);
  assert.equal(result.totalThoughts, 3);
  assert.equal(result.nextThoughtNeeded, true);
  assert.ok(Array.isArray(result.branches));
  assert.equal(typeof result.thoughtHistoryLength, "number");
  assert.equal(typeof result.rulence, "object");
  assert.equal(typeof result.rulence.decompositionAvailable, "boolean");
  assert.ok("decomposition" in result.rulence);
});

test("sequentialthinking expands totalThoughts when thoughtNumber exceeds it", () => {
  const result = sequentialThinking({
    thought: "Need more thoughts than expected.",
    nextThoughtNeeded: false,
    thoughtNumber: 7,
    totalThoughts: 3,
  });
  assert.equal(result.totalThoughts, 7);
});

test("adapter persists named traces through the Python engine", async () => {
  await callRulence(["start", "compare two options", "--session-id", "persisted", "--json"]);
  const session = await callRulence([
    "think",
    "persisted",
    "--session-id",
    "persisted",
    "--thought",
    "Assume option A is faster because it avoids network calls.",
    "--thought-number",
    "1",
    "--total-thoughts",
    "2",
    "--next-thought-needed",
    "--json",
  ]);
  assert.equal(session.session_id, "persisted");
  assert.equal(session.steps.length, 1);
});

test("preflight delegates local memory lookup to Python", async () => {
  const result = await callRulence([
    "preflight",
    "database rollback",
    "--memory-provider",
    "local",
    "--memory-path",
    join(process.cwd(), "examples", "memory.md"),
    "--json",
  ]);
  assert.ok(result.checks.some((check) => check.name === "memory_check" && check.status === "pass"));
});

test("JSON command paths stay clean on stdout", async () => {
  const commands = [
    [["classify", "debug migration", "--json"], [0]],
    [["decompose", "Build API. Then build UI.", "--json"], [0]],
    [["preflight", "delete production credentials", "--json"], [0, 2]],
    [["memory", "health", "--provider", "local", "--path", "examples/memory.md", "--json"], [0]],
    [["policy", "list", "--json"], [0]],
    [["policy", "test", "examples/policy-cases.toml", "--json"], [0]],
    [["doctor", "--json"], [0, 1]],
    [["trace", "--json"], [0]],
  ];

  for (const [args, allowed] of commands) {
    const result = await callRulence(args, allowed);
    assert.ok(result);
  }
});
