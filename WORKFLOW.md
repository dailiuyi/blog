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

Only externally registered plans may run. The watcher automatically registers new authorized ready-label requests. The controller fetches the issue again, checks its plan hash, selected route and provider consent, and validates actual model capabilities before inference. Terminal runs cannot restart because a label write failed. Both required labels are necessary for the runner; the watcher supplies symphony:acceptance after registration. Historical labels and PRs are not automatically adopted.

The controller owns checks, Git metadata, publication, labels, acceptance statuses and the shared three-repair budget. The coding agent edits only approved source/test paths, then returns ready or blocked. It must not run builds/checks, stage/commit, publish PRs, invoke GitHub writes, merge, deploy, change controller state or spawn subagents.

## Preparing an issue

The host operator starts with docs/README.md and the relevant accepted decisions in docs/DECISIONS.md. Record new lasting user decisions and update the operative specification in the same task. Keep decisions, runtime evidence and publication status distinct. This documentation duty does not expand an issue agent's approved paths or the reviewer's read-only role; the host handles cross-task document updates.

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

The automated plan covers pre-publication source and check criteria. Record local screenshots against the candidate SHA separately, and keep post-deployment online checks in a separate pending checklist until authorized deployment actually completes. Do not require future production deployment evidence to pass a pre-merge review.

With label_intake enabled, the user only needs to finish the plan, select at most one model/effort pair, then add symphony:ready last. The watcher verifies the latest ready event was added by an allowed GitHub user after intake activation and after any model/effort changes or body edits. It records the event, actor, plan and selected route; registration and enqueue happen automatically. No additional chat confirmation is needed. Selecting deepseek-flash and adding ready explicitly authorizes sending this issue's prompt and required workspace content to https://api.deepseek.com. A model label alone is not authorization.

The first watcher scan establishes a persistent activation boundary. Existing ready labels do not start work automatically. To submit one, remove and re-add ready. Repeated scans and watcher restarts do not create duplicate runs. Configuration/plan errors are reported in the issue and ready is removed; after fixing them, re-add ready. Changing the plan or model requires a fresh ready event. The user need not add symphony:acceptance.

Manual fallback remains available outside the issue workspace:

```
python3 -m symphony_acceptance --config <trusted-config.json> register --issue <number>
python3 -m symphony_acceptance --config <trusted-config.json> enqueue --issue <number>
```

Missing coding labels select gpt-6-astra / low. The independent reviewer is fixed to gpt-6-astra / high, without fallback. Manual DeepSeek registration still requires issue-specific consent before using `register --deepseek-consent`. Label intake records that consent from the authorized ready event and binds it to the issue, plan and model route. Enqueue revalidates and adds symphony:ready last.

## Acceptance

Passing local checks creates one draft PR; repairs append commits to that branch without force-push. The controller independently checks out the exact published SHA, reruns checks, then starts a fresh read-only reviewer without the coder transcript. Reports bind requirements and findings to head SHA, base SHA and plan hash. Automated reports do not fabricate a reviewer email or human GitHub approval.

Clear defects, unmet criteria and failed checks trigger automatic coding repairs with the full reviewer findings and required fixes. A reviewer returns `recapture` when only browser evidence needs supplementation: the controller replans screenshots/interactions from those findings and re-reviews the same candidate SHA without requiring source changes. Coding repairs and evidence retries share a maximum of three attempts. Style suggestions do not block. Missing prerequisites, unavailable models, scope violations, unresolved ambiguity and unchanged failed code submissions block. Same-plan re-registration and manual resume preserve prior review feedback. Infrastructure retries are bounded separately. The verifier's human `review --reviewed-by` command remains separate from automated acceptance.

Only independent acceptance and the current commit's required CI success permit symphony/acceptance=success, conversion to a non-draft PR and symphony:review. No agent merges, enables auto-merge, deploys, closes the issue or changes branch protection. For UI changes the controller opens the exact candidate build in a fresh browser, executes reviewer-planned desktop/mobile interactions, captures screenshots, and supplies them to the reviewer. Screenshots and their candidate SHA are published on the dedicated codex/acceptance-evidence branch and embedded in one authoritative PR reply. Human acceptance, when explicitly recorded, remains distinct from these automatic checks. Blocked work remains draft, gains symphony:blocked and loses symphony:ready.

## Human rework and recovery

A persistent watcher polls new ready-label requests as well as registered issues and their PRs. Only dailiuyi may use an exact `/symphony rework <reason>` or `/symphony resume` comment. Ordinary and bot comments do not dispatch. Commands are durably deduplicated; each human restart receives three repairs and preserves prior evidence. Changed plans require fresh label authorization (or manual registration with --update-plan and revalidated provider consent) after the current run has stopped.

Restarting preserves workspaces, patches, publication intents and history. Remote head mismatches never overwrite another contributor. New commits or a new base invalidate old acceptance. See docs/SYMPHONY_ACCEPTANCE.md for installation and operations.
