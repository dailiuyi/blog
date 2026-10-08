# Repository agent guidance

## Project knowledge and decision records

Read `docs/README.md` and the relevant entries in `docs/DECISIONS.md` before related work. If this older checkout lacks them, read `.local/PROJECT_CONTEXT.md` to find the active managed source worktree and installed controller; do not treat this checkout's older WORKFLOW as the current service contract. `CODEX_HANDOFF.md` is a lighting-specific handoff.

Record lasting user decisions, reasons, scope and sources in the active project's decision document during the same task, and update the relevant specification or operations guide. Before completion, check documentation impact and state whether the result is saved, committed, published and installed. Do not preserve decisions only in chat or runtime logs. Preserve existing work and avoid divergent copies. Scoped coding agents and read-only reviewers must keep their existing boundaries; the host operator handles cross-task documentation. Recording decisions grants no additional dispatch, publication, merge or deployment permission.

## GitHub issues and Symphony

For any request handled here to create or prepare an issue in `dailiuyi/blog`, apply the local `blog-symphony` skill when available. Run its status-only helper before creating the issue, then include the Symphony status when reporting the result. The helper returns exit code 1 when the runner is stopped; that status does not block ordinary issue creation.

Issue creation does not imply permission to start processing it. Start Symphony only when the user asks to start it or to dispatch a prepared issue. Before starting, inspect the current `symphony:ready` queue because startup can dispatch queued work immediately. Follow the current `WORKFLOW.md` for the issue plan, gate, model validation, and ready label; add `symphony:ready` last.

Keep pre-merge automated acceptance, local screenshot evidence, and post-deployment online acceptance separate. The workflow-plan must not require production deployment evidence before a PR can pass review. Bind screenshots to the candidate SHA; record the actual online release and leave new-version online checks pending until deployment is authorized and completed.

The owner has authorized label-driven intake. With `label_intake` enabled, an allowed GitHub user adds model/effort labels first and `symphony:ready` last on a prepared issue. This action authorizes that issue's selected route, including sending its prompt and required workspace content to `https://api.deepseek.com` when `symphony:model:deepseek-flash` is selected. The watcher records the actor, event, plan and route, then registers and enqueues automatically; do not request an additional chat confirmation for this action. A model label alone or a historical ready label is insufficient. `symphony:acceptance` is controller-managed. Inspect the running service's installed WORKFLOW.md when it differs from this checkout. Preserve existing Symphony workspaces and report build checks separately from live deployment.
