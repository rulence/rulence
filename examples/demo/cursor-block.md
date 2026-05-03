# Cursor Block Demo

Task:

```text
delete production credentials
```

Setup:

```bash
rulence policy install secrets aws
rulence install cursor --dir .
```

Expected result:

```text
verdict: block
tier: Tier 5 / high-risk
```

For the video, show `.cursor/rules/rulence.md`, then ask Cursor to perform the
same task. The rule instructs the agent to call Rulence before risky tool use.
