# Workspace shape: monitoring

Periodic digest / monitoring workflow. Used as agent prompt material when the
user wants to "watch X and tell me when something changes".

## When to propose

- The work is recurring (daily/weekly/monthly)
- There's a clear data source to poll
- Output is a digest, alert, or trend report

## Suggested stages

| id | name | purpose |
|---|---|---|
| 01-collect | collect | Pull fresh data from the source |
| 02-summarize | summarize | Reduce raw data to a digest |
| 03-route | route | Send to wherever it needs to go |

## Tags

Often `recurring`, `digest`, plus a domain tag.
