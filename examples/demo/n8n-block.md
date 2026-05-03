# n8n Block Demo

Task:

```text
delete production credentials
```

Setup:

```bash
rulence policy install secrets aws
rulence install n8n
```

Paste the returned MCP server config into n8n's MCP settings.

Expected result:

```text
verdict: block
tier: Tier 5 / high-risk
```

For the video, send the same task through the n8n MCP node and show the
Rulence `structuredContent` block response.
