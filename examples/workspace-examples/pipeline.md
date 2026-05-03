# Workspace shape: pipeline

Generic 3-stage pipeline. Used as agent prompt material when the user's intent
suggests "do something, end-to-end, with a clear handoff between phases".

## When the bootstrap agent should propose this shape

- User's intent has a clear input (something to study) and output (something to produce)
- Work fits in 1-3 sessions per stage
- Stages have natural review points

## Suggested stages

| id | name | purpose |
|---|---|---|
| 01-discover | discover | Survey, gather inputs, define success |
| 02-execute | execute | Do the actual work |
| 03-integrate | integrate | Promote outputs, record learnings to viking |

## Tags that often apply

Pipeline workspaces frequently use tags like `wip`, `hands-on`, plus a domain
tag (e.g., `transformer`, `writing`, `data-cleaning`).
