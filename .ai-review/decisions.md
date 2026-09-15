# Decisions

- Use upstream MIT implementation rather than reverse-engineering a new client.
- Reuse the desktop client's local session; never copy or print access tokens.
- Run through Windows Task Scheduler with a daily job and a catch-up poll job.
