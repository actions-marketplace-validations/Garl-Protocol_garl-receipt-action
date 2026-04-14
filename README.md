# GARL Receipt — GitHub Action

> **Cryptographic proof for every AI-generated commit.**

Signs every AI-authored commit in a pull request (Claude Code, Cursor,
GitHub Copilot, Aider, Codex) with an ECDSA-secp256k1 signature on the
open-source [GARL Protocol](https://garl.ai) ledger, and posts a
sticky PR comment + neutral GitHub check with a shareable receipt URL
for each commit.

Five lines of YAML. Two repo secrets. No diffs or source are ever
uploaded — only metadata.

## What reviewers see on a PR

```
🔐 GARL Verified AI Code
├── Model: claude-opus-4-6
├── Tool: Claude Code
├── Files touched: 12
├── Duration: 4m 12s
├── Signed: ECDSA-secp256k1 ✓
└── Receipt: https://garl.ai/r/a8f3c2d1
```

Plus a rolling sticky PR comment:

> **3 of 5 commits** signed as AI-authored.
> Breakdown: 2 Claude Code, 1 GitHub Copilot

And an informational (neutral) GitHub check named `GARL Receipt`.

[Live receipt example →](https://garl.ai/r/6ff83db8)

## Setup (5 lines of YAML)

1. Register a repo agent via the
   [`garl_register_agent`](https://garl.ai/docs#mcp-server) MCP tool
   or `curl`:

   ```bash
   curl -sX POST https://api.garl.ai/api/v1/agents/auto-register \
     -H "Content-Type: application/json" \
     -d '{"name":"gh-<owner>-<repo>","framework":"github-action"}'
   ```

   Save `agent_id` and `api_key` from the response.

2. Add two repository secrets:
   - `GARL_AGENT_ID` — the returned agent UUID
   - `GARL_API_KEY` — the returned API key

3. Add the workflow (`.github/workflows/garl-receipt.yml`):

   ```yaml
   name: GARL Receipt
   on:
     pull_request:
       types: [opened, synchronize, reopened]
   jobs:
     sign:
       runs-on: ubuntu-latest
       permissions:
         contents: read
         pull-requests: write
         checks: write
       steps:
         - uses: actions/checkout@v4
           with:
             fetch-depth: 0  # needed so git log can walk base..head
         - uses: Garl-Protocol/garl-receipt-action@v1.0.0
           with:
             garl-api-key: ${{ secrets.GARL_API_KEY }}
             garl-agent-id: ${{ secrets.GARL_AGENT_ID }}
   ```

Open a PR whose commits carry an AI co-author trailer — the action
signs them.

## Inputs

| Name | Required | Default | Purpose |
|---|---|---|---|
| `garl-api-key` | ✅ | — | Repo agent API key (secret) |
| `garl-agent-id` | ✅ | — | Repo agent UUID (secret) |
| `min-confidence` | | `0.5` | Lowest AI-authorship confidence (0.0–1.0) that produces a receipt. Commits below this are summarized but not signed. |
| `comment` | | `true` | Post/update a sticky PR comment with the receipt summary. |
| `check` | | `true` | Post an informational (neutral) GitHub check run on the PR. |
| `api-url` | | `https://api.garl.ai/api/v1` | GARL API base URL (override for self-hosted). |
| `site-url` | | `https://garl.ai` | Frontend base URL used in receipt links. |

## Outputs

| Name | Description |
|---|---|
| `receipts-json` | JSON array of `{commit, tool, confidence, model, receipt_url}` for each signed commit. |
| `ai-commit-count` | Integer count of commits signed in this run. |

## Detection rules

The action reads each commit's subject + body and scores AI authorship:

| Signal | Confidence |
|---|---|
| `Co-Authored-By: ...Claude` / `...Cursor` / `...GitHub Copilot` / `...aider` / `...codex` | **1.0** |
| `Generated with [Claude Code]` | 0.9 |
| Explicit model name (`claude-opus-4-6`, `gpt-4.1-mini`, etc.) | 0.6–0.7 |
| `🤖 Generated with ...` (emoji marker) | 0.6 |
| `cursor` bare heuristic | 0.4 |

Commits below `min-confidence` are listed as *no AI marker* but never
fail the workflow. The check run is always **neutral** —
informational, non-blocking.

## Privacy & data

Only metadata is sent to GARL:

- commit SHA, subject, files-changed count
- detected AI tool + confidence + model name (if any)
- commit duration (git committer date − author date)

**Source code, diffs, and file contents are never uploaded.** Receipts
only surface task description, status, duration, category, and
hashes — never `input_summary` / `output_summary`.

## Why?

46% of new code is AI-generated. Git history captures the human
author but not the model, the prompt, or the verifier. GARL Receipt
closes that provenance gap — for reviewers, for auditors, and for
[EU AI Act Article 50](https://eur-lex.europa.eu/eli/reg/2024/1689/oj) compliance.

## See also

- [GARL Protocol](https://github.com/Garl-Protocol/garl) — the open trust layer this action builds on
- [garl.ai/for-code](https://garl.ai/for-code) — product landing page
- [garl.ai/docs#receipts](https://garl.ai/docs#receipts) — protocol docs

Apache 2.0 · Part of the [GARL Protocol](https://garl.ai) ecosystem.
