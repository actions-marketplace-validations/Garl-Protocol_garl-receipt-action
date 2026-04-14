# Changelog — garl-receipt-action

## 1.0.0 — 2026-04-14

First standalone release, extracted from the
[GARL Protocol monorepo](https://github.com/Garl-Protocol/garl) at
commit `a220049` (including the Cloudflare User-Agent fix discovered
during dogfood testing).

### Features
- Detects AI-authored commits via co-author trailers (Claude, Cursor,
  GitHub Copilot, Aider, Codex), `Generated with [Claude Code]`
  markers, and model-name heuristics (confidence 0.4–1.0).
- Submits a signed trace to `/api/v1/verify` for every commit above
  `min-confidence` (default 0.5) using a per-repo GARL agent
  (`GARL_API_KEY` + `GARL_AGENT_ID` secrets).
- Upserts a single sticky PR comment summarizing
  `N of M commits signed · breakdown by tool`.
- Publishes an informational (neutral, never-failing) `GARL Receipt`
  check run with the same summary.
- Exposes `receipts-json` + `ai-commit-count` outputs for downstream
  workflow steps.

### Privacy
Only commit metadata (SHA, subject, files-changed count, detected
tool + confidence + model) is uploaded. **No diffs, no file contents.**
