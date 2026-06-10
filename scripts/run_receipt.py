#!/usr/bin/env python3
"""GARL Receipt — GitHub Action runtime.

Detects AI-authored commits in a pull request (or a single push) using
co-author trailers and well-known generator signatures, submits a
signed trace to GARL for each qualifying commit, and posts a
consolidated receipt summary as a PR comment + informational check.

Inputs come from environment variables populated by action.yml.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any


# ──────────────────────────────────────────────────────────────────
# Config / helpers
# ──────────────────────────────────────────────────────────────────

API_URL = os.environ.get("GARL_API_URL", "https://api.garl.ai/api/v1").rstrip("/")
SITE_URL = os.environ.get("GARL_SITE_URL", "https://garl.ai").rstrip("/")
API_KEY = os.environ.get("GARL_API_KEY", "")
AGENT_ID = os.environ.get("GARL_AGENT_ID", "")
MIN_CONF = float(os.environ.get("GARL_MIN_CONF", "0.5") or 0.5)
POST_COMMENT = (os.environ.get("GARL_POST_COMMENT", "true").lower() == "true")
POST_CHECK = (os.environ.get("GARL_POST_CHECK", "true").lower() == "true")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_EVENT_PATH = os.environ.get("GITHUB_EVENT_PATH", "")
GITHUB_EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")  # "owner/repo"
GITHUB_OUTPUT = os.environ.get("GITHUB_OUTPUT", "")
GITHUB_STEP_SUMMARY = os.environ.get("GITHUB_STEP_SUMMARY", "")
GITHUB_SHA = os.environ.get("GITHUB_SHA", "")


def log(msg: str) -> None:
    print(msg, flush=True)


def fail(msg: str) -> None:
    print(f"::error::{msg}", flush=True)
    sys.exit(1)


def write_output(name: str, value: str) -> None:
    if not GITHUB_OUTPUT:
        return
    with open(GITHUB_OUTPUT, "a", encoding="utf-8") as fh:
        if "\n" in value:
            token = "EOF_GARL"
            fh.write(f"{name}<<{token}\n{value}\n{token}\n")
        else:
            fh.write(f"{name}={value}\n")


def write_summary(markdown: str) -> None:
    if not GITHUB_STEP_SUMMARY:
        return
    with open(GITHUB_STEP_SUMMARY, "a", encoding="utf-8") as fh:
        fh.write(markdown.rstrip() + "\n")


# ──────────────────────────────────────────────────────────────────
# AI-authorship detection
# ──────────────────────────────────────────────────────────────────

# (regex, tool, confidence). More specific patterns should appear first;
# we take the highest-confidence match per commit.
AI_PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    # Explicit co-author trailers (high confidence)
    (re.compile(r"Co-Authored-By:.*Claude", re.IGNORECASE), "Claude", 1.0),
    (re.compile(r"Co-Authored-By:.*Cursor", re.IGNORECASE), "Cursor", 1.0),
    (re.compile(r"Co-Authored-By:.*(GitHub Copilot|copilot)", re.IGNORECASE), "GitHub Copilot", 1.0),
    (re.compile(r"Co-Authored-By:.*aider", re.IGNORECASE), "Aider", 1.0),
    (re.compile(r"Co-Authored-By:.*codex", re.IGNORECASE), "OpenAI Codex", 1.0),
    # Embedded markers (medium–high)
    (re.compile(r"Generated with \[Claude Code\]", re.IGNORECASE), "Claude Code", 0.9),
    (re.compile(r"🤖 Generated with", re.IGNORECASE), "AI (generic marker)", 0.6),
    # Model-name mention anywhere in the message (heuristic, lower)
    (re.compile(r"\bclaude-(opus|sonnet|haiku)[-a-z0-9.]*", re.IGNORECASE), "Claude", 0.7),
    (re.compile(r"\bgpt-[0-9]+(\.[0-9]+)?[-a-z0-9]*", re.IGNORECASE), "OpenAI GPT", 0.6),
    (re.compile(r"\bcursor\b", re.IGNORECASE), "Cursor (heuristic)", 0.4),
]

MODEL_REGEX = re.compile(r"\b(claude-[a-z0-9.-]+|gpt-[a-z0-9.-]+|o[13]-[a-z0-9.-]+|gemini-[a-z0-9.-]+)\b", re.IGNORECASE)


def detect(message: str) -> tuple[str, float, str | None]:
    """Return (tool, confidence, model_or_none)."""
    best_tool = "unknown"
    best_conf = 0.0
    for rx, tool, conf in AI_PATTERNS:
        if rx.search(message) and conf > best_conf:
            best_tool = tool
            best_conf = conf
    model_match = MODEL_REGEX.search(message)
    model = model_match.group(0) if model_match else None
    return best_tool, best_conf, model


# ──────────────────────────────────────────────────────────────────
# Git interrogation
# ──────────────────────────────────────────────────────────────────

def run_git(*args: str) -> str:
    try:
        out = subprocess.check_output(["git", *args], stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        log(f"git {' '.join(args)} failed: {e.output.decode(errors='replace')}")
        return ""
    return out.decode("utf-8", errors="replace")


def load_event() -> dict[str, Any]:
    if not GITHUB_EVENT_PATH or not os.path.exists(GITHUB_EVENT_PATH):
        return {}
    with open(GITHUB_EVENT_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def list_commits(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a list of commit dicts with sha/message/files_changed/duration_ms."""
    commits: list[dict[str, Any]] = []

    if GITHUB_EVENT_NAME == "pull_request":
        pr = event.get("pull_request", {})
        base_sha = pr.get("base", {}).get("sha", "")
        head_sha = pr.get("head", {}).get("sha", "") or GITHUB_SHA
        if not base_sha or not head_sha:
            return []
        shas = [s for s in run_git("log", "--format=%H", f"{base_sha}..{head_sha}").splitlines() if s]
    elif GITHUB_EVENT_NAME == "push":
        shas = [c.get("id", "") for c in event.get("commits", []) if c.get("id")]
        if not shas and GITHUB_SHA:
            shas = [GITHUB_SHA]
    else:
        shas = [GITHUB_SHA] if GITHUB_SHA else []

    for sha in shas:
        if not sha:
            continue
        subject = run_git("show", "-s", "--format=%s", sha).strip()
        body = run_git("show", "-s", "--format=%B", sha).strip()
        author_ts = run_git("show", "-s", "--format=%at", sha).strip()
        committer_ts = run_git("show", "-s", "--format=%ct", sha).strip()
        # files changed in this commit only (ignores merges)
        files_out = run_git("show", "--name-only", "--pretty=format:", sha).strip()
        files = [f for f in files_out.splitlines() if f.strip()]
        try:
            duration_ms = max(0, (int(committer_ts) - int(author_ts)) * 1000) if author_ts and committer_ts else 0
        except ValueError:
            duration_ms = 0
        commits.append({
            "sha": sha,
            "subject": subject,
            "body": body,
            "files": files,
            "files_changed": len(files),
            "duration_ms": duration_ms,
        })

    return commits


# ──────────────────────────────────────────────────────────────────
# GARL API
# ──────────────────────────────────────────────────────────────────

USER_AGENT = "garl-receipt-action/1.1 (+https://garl.ai/for-code)"


def http_json(url: str, method: str = "GET", headers: dict[str, str] | None = None, body: Any = None) -> tuple[int, Any]:
    data = None
    hdrs = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,  # avoid Cloudflare default-UA bans (error 1010)
    }
    if headers:
        hdrs.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                return resp.status, {"_raw": payload}
    except urllib.error.HTTPError as e:
        payload = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
        try:
            return e.code, json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            return e.code, {"_raw": payload}
    except urllib.error.URLError as e:
        return 0, {"_error": str(e)}


def commit_ci_conclusion(sha: str) -> tuple[bool, str]:
    """Read the commit's REAL CI result from GitHub so the receipt carries an
    independently-verifiable attestation instead of a hardcoded 'success'.

    Returns (commit_exists, conclusion) where conclusion is one of:
    pending | failure | success | neutral | none | unknown. GARL's own
    check-run is excluded so we read the repo's CI, not ourselves.
    """
    if not (GITHUB_TOKEN and GITHUB_REPOSITORY and sha):
        return True, "unknown"
    st, _ = http_json(f"{GH_API}/repos/{GITHUB_REPOSITORY}/commits/{sha}", headers=gh_headers())
    if st == 404:
        return False, "none"
    if st != 200:
        return True, "unknown"
    st, data = http_json(f"{GH_API}/repos/{GITHUB_REPOSITORY}/commits/{sha}/check-runs", headers=gh_headers())
    if st != 200 or not isinstance(data, dict):
        return True, "unknown"
    runs = [c for c in (data.get("check_runs") or []) if "garl" not in (c.get("name") or "").lower()]
    if not runs:
        return True, "none"
    statuses = {c.get("status") for c in runs}
    conclusions = {c.get("conclusion") for c in runs}
    if statuses & {"queued", "in_progress"} or None in conclusions:
        return True, "pending"
    if conclusions & {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}:
        return True, "failure"
    if "success" in conclusions:
        return True, "success"
    return True, "neutral"


def submit_trace(commit: dict[str, Any], tool: str, confidence: float, model: str | None) -> dict[str, Any] | None:
    task = f"AI-authored commit {commit['sha'][:7]}: {commit['subject'][:160]}"
    exists, ci = commit_ci_conclusion(commit["sha"])
    metadata = {
        "github_repo": GITHUB_REPOSITORY,
        "commit_sha": commit["sha"],
        "ai_tool": tool,
        "ai_confidence": confidence,
        "files_changed": commit["files_changed"],
    }
    if model:
        metadata["model"] = model
    # A commit whose CI actually failed must NOT be recorded as a success.
    status = "failure" if ci == "failure" else "success"
    # An independently re-verifiable attestation: anyone can call GitHub with
    # repo + commit_sha and confirm this conclusion. With ENABLE_GITHUB_
    # ATTESTATION_CHECK on, the GARL backend also re-verifies and stamps it.
    attestation: dict[str, Any] = {
        "type": "github-check-run",
        "repo": GITHUB_REPOSITORY,
        "commit_sha": commit["sha"],
        "conclusion": ci,
    }
    if GITHUB_REPOSITORY:
        attestation["url"] = f"https://github.com/{GITHUB_REPOSITORY}/commit/{commit['sha']}"
    body = {
        "agent_id": AGENT_ID,
        "task_description": task,
        "status": status,
        "duration_ms": max(commit["duration_ms"], 1),
        "category": "coding",
        "runtime_env": f"github-action-receipt/{tool.lower().replace(' ', '-')}",
        "metadata": metadata,
        "attestations": [attestation],
    }
    status, data = http_json(
        f"{API_URL}/verify",
        method="POST",
        headers={"x-api-key": API_KEY},
        body=body,
    )
    if status != 200:
        log(f"::warning::GARL /verify returned {status} for {commit['sha'][:7]}: {data}")
        return None
    return data


# ──────────────────────────────────────────────────────────────────
# GitHub API — comment + check
# ──────────────────────────────────────────────────────────────────

GH_API = "https://api.github.com"

def gh_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


COMMENT_MARKER = "<!-- garl-receipt-action -->"


def post_pr_comment(event: dict[str, Any], body_md: str) -> None:
    if not GITHUB_TOKEN or GITHUB_EVENT_NAME != "pull_request":
        return
    pr_number = event.get("pull_request", {}).get("number")
    if not pr_number or not GITHUB_REPOSITORY:
        return

    # Upsert: find existing comment with marker and update, else create
    list_url = f"{GH_API}/repos/{GITHUB_REPOSITORY}/issues/{pr_number}/comments?per_page=100"
    status, data = http_json(list_url, headers=gh_headers())
    existing_id = None
    if status == 200 and isinstance(data, list):
        for c in data:
            if isinstance(c, dict) and isinstance(c.get("body"), str) and COMMENT_MARKER in c["body"]:
                existing_id = c.get("id")
                break

    payload = {"body": f"{COMMENT_MARKER}\n{body_md}"}
    if existing_id:
        url = f"{GH_API}/repos/{GITHUB_REPOSITORY}/issues/comments/{existing_id}"
        http_json(url, method="PATCH", headers=gh_headers(), body=payload)
    else:
        url = f"{GH_API}/repos/{GITHUB_REPOSITORY}/issues/{pr_number}/comments"
        http_json(url, method="POST", headers=gh_headers(), body=payload)


def post_check(head_sha: str, ai_count: int, total: int, summary_md: str) -> None:
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY or not head_sha:
        return
    url = f"{GH_API}/repos/{GITHUB_REPOSITORY}/check-runs"
    title = f"{ai_count}/{total} AI-authored commits signed"
    payload = {
        "name": "GARL Receipt",
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": "neutral",
        "output": {
            "title": title,
            "summary": summary_md[:60000],  # GitHub cap
        },
    }
    http_json(url, method="POST", headers=gh_headers(), body=payload)


# ──────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────

def main() -> None:
    if not API_KEY:
        fail("GARL_API_KEY not set. Add it as a repository secret.")
    if not AGENT_ID:
        fail("GARL_AGENT_ID not set. Generate a GARL agent UUID and add it as a repository secret.")

    event = load_event()
    commits = list_commits(event)
    if not commits:
        log("No commits found to scan.")
        write_output("receipts-json", "[]")
        write_output("ai-commit-count", "0")
        write_summary("## 🔐 GARL Receipt\n\nNo commits were scanned in this event.")
        return

    receipts: list[dict[str, Any]] = []
    tool_counter: dict[str, int] = {}
    lines: list[str] = []

    for commit in commits:
        tool, conf, model = detect(f"{commit['subject']}\n\n{commit['body']}")
        commit_line = f"- `{commit['sha'][:7]}` {commit['subject'][:100]}"

        if conf < MIN_CONF:
            lines.append(f"{commit_line} — _no AI marker (confidence {conf:.2f})_")
            continue

        tool_counter[tool] = tool_counter.get(tool, 0) + 1
        result = submit_trace(commit, tool, conf, model)
        if not result:
            lines.append(f"{commit_line} — detected **{tool}** but trace submission failed")
            continue

        receipt_url = result.get("receipt_url") or f"{SITE_URL}/r/{(result.get('trace_hash') or '')[:8]}"
        files_changed = commit["files_changed"]
        dur_s = commit["duration_ms"] // 1000
        dur_str = f"{dur_s // 60}m {dur_s % 60}s" if dur_s >= 60 else f"{dur_s}s" if dur_s else "—"
        receipts.append({
            "commit": commit["sha"],
            "tool": tool,
            "confidence": conf,
            "model": model,
            "receipt_url": receipt_url,
        })
        block = (
            f"\n<details><summary>🔐 <code>{commit['sha'][:7]}</code> · {tool}"
            f"{f' · {model}' if model else ''} · [{receipt_url.rsplit('/', 1)[-1]}]({receipt_url})</summary>\n\n"
            f"```\n"
            f"🔐 GARL Verified AI Code\n"
            f"├── Model: {model or 'unspecified'}\n"
            f"├── Tool: {tool}\n"
            f"├── Files touched: {files_changed}\n"
            f"├── Duration: {dur_str}\n"
            f"├── Signed: ECDSA-secp256k1 ✓\n"
            f"└── Receipt: {receipt_url}\n"
            f"```\n\n"
            f"_Commit: `{commit['sha']}` · {commit['subject']}_\n"
            f"</details>"
        )
        lines.append(f"{commit_line} — **{tool}** ✓{block}")

    ai_count = len(receipts)
    total = len(commits)

    tool_breakdown = ", ".join(f"{n} {t}" for t, n in sorted(tool_counter.items(), key=lambda x: -x[1])) or "—"
    summary = (
        f"## 🔐 GARL Receipt\n\n"
        f"**{ai_count} of {total} commits** signed as AI-authored.  \n"
        f"Breakdown: {tool_breakdown}\n\n"
        + "\n".join(lines)
        + f"\n\n_Powered by [GARL Protocol]({SITE_URL}) — cryptographic receipts for AI-generated code._"
    )

    write_summary(summary)
    write_output("receipts-json", json.dumps(receipts, separators=(",", ":")))
    write_output("ai-commit-count", str(ai_count))

    if POST_COMMENT:
        post_pr_comment(event, summary)

    if POST_CHECK:
        head_sha = event.get("pull_request", {}).get("head", {}).get("sha", "") or GITHUB_SHA
        post_check(head_sha, ai_count, total, summary)


if __name__ == "__main__":
    main()
