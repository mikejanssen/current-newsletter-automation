# RSS Watch Scheduler

This Cloudflare Worker replaces GitHub Actions cron scheduling, which repeatedly
delayed RSS Watch triggers by hours. Cloudflare Cron Triggers dispatch the
existing `rss-watch.yml` workflow through GitHub's API.

The worker schedules three attempts for each weekday delivery. The workflow's
persisted daily completion marker allows only the first successful attempt to
post to Slack. Both the worker and workflow reject stale scheduled deliveries
outside the intended Eastern-time window.

The six daily UTC checks are consolidated into two cron patterns to stay within
Cloudflare's free-plan trigger limit. The worker filters those checks to the
three intended Eastern-time attempts for each delivery, including daylight
saving time changes.

## Configuration

Set `GITHUB_ACTIONS_TOKEN` as a Cloudflare Worker secret. Use a fine-grained
GitHub token restricted to `current-newsletter-automation` with **Actions:
Read and write** permission.

```bash
npx wrangler secret put GITHUB_ACTIONS_TOKEN
npm test
npm run deploy
```

Manual workflow runs remain available in GitHub and bypass scheduled-delivery
deduplication unless the `scheduled_delivery` input is selected.
