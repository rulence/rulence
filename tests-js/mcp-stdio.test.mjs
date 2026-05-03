import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

test("stdio MCP server lists and calls sequentialthinking", async () => {
  const sessionDir = mkdtempSync(join(tmpdir(), "rulence-mcp-stdio-"));
  const transport = new StdioClientTransport({
    command: "node",
    args: [resolve("src-js/mcp-server.js")],
    cwd: process.cwd(),
    stderr: "pipe",
    env: {
      ...process.env,
      RULENCE_SESSION_DIR: sessionDir,
      DISABLE_THOUGHT_LOGGING: "true",
    },
  });
  const client = new Client({ name: "rulence-test-client", version: "0.0.0" });

  try {
    await client.connect(transport);
    const tools = await client.listTools();
    assert.ok(tools.tools.some((tool) => tool.name === "sequentialthinking"));

    const result = await client.callTool({
      name: "sequentialthinking",
      arguments: {
        thought: "Start with the riskiest assumption.",
        nextThoughtNeeded: false,
        thoughtNumber: 1,
        totalThoughts: 1,
      },
    });

    assert.equal(result.structuredContent.thoughtNumber, 1);
    assert.equal(result.structuredContent.totalThoughts, 1);
    assert.equal(result.structuredContent.nextThoughtNeeded, false);
    assert.ok(result.structuredContent.rulence);
    assert.equal(typeof result.structuredContent.rulence.decompositionAvailable, "boolean");
    assert.ok("decomposition" in result.structuredContent.rulence);

    const classified = await client.callTool({
      name: "rulence_classify",
      arguments: {
        task: "debug the failing migration",
      },
    });
    assert.equal(classified.structuredContent.tier, JSON.parse(classified.content[0].text).tier);
  } finally {
    await client.close();
  }
});
