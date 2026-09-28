# Repository agent guidance

## Project knowledge and decision records

Start with `docs/README.md`, then read the relevant entries in `docs/DECISIONS.md` and the task's specification. `CODEX_HANDOFF.md` is a lighting implementation handoff, not the general project entry point. For live Symphony behavior, compare the installed `WORKFLOW.md` with this checkout before relying on either version.

When the user makes or changes a lasting project decision, update its decision record in the same task: preserve the user's intent, source, scope, reason and status, and link the operative specification or implementation. Update existing records instead of leaving conflicting copies. Record proposed choices as proposed; do not turn an agent's implementation default into a user decision. Do not copy whole chat transcripts, credentials or private execution logs into project documents.

Before reporting completion, check whether the task changed a project decision, specification, operation or acceptance procedure and update the relevant documents. Report separately whether files are saved, committed, published and installed. A local runtime release does not publish the source, and a PR comment is not the only long-term project record. This checkpoint is an agent responsibility, not a claim that passing tests detects every undocumented decision.

The host operator owns cross-task decision records. A scoped coding agent stays within its approved paths; a read-only reviewer reports documentation gaps rather than editing files or expanding scope. Writing a decision record does not grant new dispatch, provider, merge or deployment authorization.

## GitHub issues and Symphony

For any request handled here to create or prepare an issue in `dailiuyi/blog`, apply the local `blog-symphony` skill when available. Run its status-only helper before creating the issue, then include the Symphony status when reporting the result. The helper returns exit code 1 when the runner is stopped; that status does not block ordinary issue creation.

Issue creation does not imply permission to start processing it. Start Symphony only when the user asks to start it or to dispatch a prepared issue. Before starting, inspect the current `symphony:ready` queue because startup can dispatch queued work immediately. Follow the current `WORKFLOW.md` for the issue plan, gate, model validation, and ready label; add `symphony:ready` last.

Keep pre-merge automated acceptance, local screenshot evidence, and post-deployment online acceptance separate. The workflow-plan must not require production deployment evidence before a PR can pass review. Bind screenshots to the candidate SHA; record the actual online release and leave new-version online checks pending until deployment is authorized and completed.

The owner has authorized label-driven intake. With `label_intake` enabled, an allowed GitHub user adds model/effort labels first and `symphony:ready` last on a prepared issue. This action authorizes that issue's selected route, including sending its prompt and required workspace content to `https://api.deepseek.com` when `symphony:model:deepseek-flash` is selected. The watcher records the actor, event, plan and route, then registers and enqueues automatically; do not request an additional chat confirmation for this action. A model label alone or a historical ready label is insufficient. `symphony:acceptance` is controller-managed. Preserve existing Symphony workspaces and report build checks separately from live deployment.
