---
tracker:
  kind: github
  provider:
    repo: dailiuyi/blog
    token: $GITHUB_TOKEN
  active_states: [open]
  terminal_states: [closed]
  required_labels: ["symphony:ready", "symphony:acceptance"]
polling:
  interval_ms: 30000
workspace:
  root: /root/.local/symphony-workspaces-blog
hooks:
  after_create: |
    set -eu
    git clone --depth 1 https://github.com/dailiuyi/blog.git .
    git config --local core.autocrlf input
    git config user.name "Symphony Controller"
    git config user.email "symphony-controller@users.noreply.github.com"
  timeout_ms: 600000
agent:
  max_concurrent_agents: 2
  max_turns: 1
  max_retry_backoff_ms: 300000
codex:
  command: 'env PATH="$HOME/.local/symphony-bin:$PATH" PYTHONPATH="$HOME/.local/symphony-acceptance/current/scripts" python3 -m symphony_acceptance --config "$HOME/.local/symphony-acceptance/current/config/symphony-blog.json" bridge'
  approval_policy: never
  thread_sandbox: workspace-write
  turn_sandbox_policy:
    type: workspaceWrite
    networkAccess: true
  turn_timeout_ms: 14400000
  read_timeout_ms: 60000
  stall_timeout_ms: 0
---

[SYMPHONY_ROUTING_V1]
issue={{ issue.identifier | url_encode }}
{% for label in issue.labels %}label={{ label | url_encode }}
{% endfor %}[/SYMPHONY_ROUTING_V1]
Handle GitHub issue {{ issue.identifier }}: {{ issue.title }}.
URL: {{ issue.url }}

## Trusted controller

Only externally registered plans may run. The controller fetches the issue again, checks its plan hash and provider consent, and validates actual model capabilities before inference. Terminal runs cannot restart because a label write failed. Both required labels are necessary; historical issues and PRs are not automatically adopted.

The controller owns checks, Git metadata, publication, labels, acceptance statuses and the shared three-repair budget. The coding agent edits only approved source/test paths, then returns ready or blocked. It must not run builds/checks, stage/commit, publish PRs, invoke GitHub writes, merge, deploy, change controller state or spawn subagents.

## Preparing an issue

An operator puts exactly one scoped plan in the issue body:

<!-- workflow-plan
{
  "title": "Describe the requested change",
  "acceptance": "The requested behavior is implemented.\nThe required repository checks pass.",
  "allowedPaths": ["src/content/blog/**"],
  "checks": ["site", "stats", "release"]
}
-->

Every plan requires site, stats and release; add motion when the allowed paths can touch motion modules or tests. Trusted repository config defines commands and protected paths. The existing `node scripts/workflow_verify.mjs gate --issue-body <issue.md>` remains a blog plan gate. Controller registration additionally validates repository-specific checks and model routes.

Run the installed controller outside the issue workspace:

```
python3 -m symphony_acceptance --config <trusted-config.json> register --issue <number>
python3 -m symphony_acceptance --config <trusted-config.json> enqueue --issue <number>
```

Missing coding labels select gpt-6-astra / low; at most one model and effort label are allowed. The independent reviewer is fixed to gpt-6-astra / high, without fallback. A DeepSeek route requires explicit consent for this issue's prompt and workspace content to https://api.deepseek.com; only then may the operator use `register --deepseek-consent`. Consent is bound to the issue and plan hash. Enqueue revalidates and adds symphony:ready last.

## Acceptance

Passing local checks creates one draft PR; repairs append commits to that branch without force-push. The controller independently checks out the exact published SHA, reruns checks, then starts a fresh read-only reviewer without the coder transcript. Reports bind requirements and findings to head SHA, base SHA and plan hash. Automated reports do not fabricate a reviewer email or human GitHub approval.

Clear defects, unmet criteria and failed checks trigger at most three further coding repairs. Style suggestions do not block. Missing prerequisites, unavailable models, scope violations and unchanged failed submissions block. Infrastructure retries are bounded separately. The old checker-only two-failure cap is removed: the controller owns the combined budget. The verifier's human `review --reviewed-by` command remains separate from automated acceptance.

Only independent acceptance and the current commit's required CI success permit symphony/acceptance=success, conversion to a non-draft PR and symphony:review. No agent merges, enables auto-merge, deploys, closes the issue or changes branch protection. Page visuals remain for human acceptance. Blocked work remains draft, gains symphony:blocked and loses symphony:ready.

## Human rework and recovery

A persistent watcher polls only registered issues and their PRs. Only dailiuyi may use an exact `/symphony rework <reason>` or `/symphony resume` comment. Ordinary and bot comments do not dispatch. Commands are durably deduplicated; each human restart receives three repairs and preserves prior evidence. Changed plans require operator registration with --update-plan and revalidated provider consent.

Restarting preserves workspaces, patches, publication intents and history. Remote head mismatches never overwrite another contributor. New commits or a new base invalidate old acceptance. See docs/SYMPHONY_ACCEPTANCE.md for installation and operations.
