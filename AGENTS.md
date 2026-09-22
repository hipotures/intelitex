# Repository agent instructions

## Intelitex web tasks

Before implementing, reviewing, or debugging Intelitex web UI, API-facing workflow,
SSE, Review, Reader, or a related visual/state regression, read
[the intelitex-web skill](.agents/skills/intelitex-web/SKILL.md) and its relevant references.
For unrelated tasks do not load the complete web specification.

The original v33 asset defines appearance; Python/application/API defines actual state.
Do not silently treat proposed endpoints or unresolved product policies as implemented.
The current user request defines scope; the skill does not authorize an unrelated rewrite.

Use `uv`, never direct `pip`. Preserve unrelated changes. Never use live model calls
in implementation tests without separate explicit authorization. Report test results
only when executed. Commit focused requested changes; push only when instructed.


GitHub CLI:
Do not use:
  gh issue view <n> --repo <repo>

because the default GraphQL query may request deprecated
Projects Classic projectCards and fail.

For reading issues, prefer:
  gh api repos/<owner>/<repo>/issues/<n> --jq '.body'

For structured metadata:
  gh api repos/<owner>/<repo>/issues/<n> \
    --jq '{number,title,state,html_url,body}'