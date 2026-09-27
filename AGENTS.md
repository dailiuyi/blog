# Repository agent guidance

## GitHub issues and Symphony

For any request handled here to create or prepare an issue in `dailiuyi/blog`, apply the local `blog-symphony` skill when available. Run its status-only helper before creating the issue, then include the Symphony status when reporting the result. The helper returns exit code 1 when the runner is stopped; that status does not block ordinary issue creation.

Issue creation does not imply permission to start processing it. Start Symphony only when the user asks to start it or to dispatch a prepared issue. Before starting, inspect the current `symphony:ready` queue because startup can dispatch queued work immediately. Follow the current `WORKFLOW.md` for the issue plan, gate, model validation, and ready label; add `symphony:ready` last.

For `symphony:model:deepseek-flash`, obtain explicit consent for that specific issue before dispatching its prompt and workspace content to `https://api.deepseek.com`. A model label or consent for a different issue is insufficient. Preserve existing Symphony workspaces and report build checks separately from live deployment.
