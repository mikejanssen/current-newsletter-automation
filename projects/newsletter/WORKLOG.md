## Worklog — Newsletter Automation

### Current state (as of 2026-01-30)
- Script: `projects/newsletter/make-newsletter-draft.py`
- It still pulls WordPress posts and can generate per-item HTML snippets.
- Mailchimp + Parse.ly integration is still in the script, but the plan is to drop both.
- A new flag was added: `--output-items /path/to/file` to write one HTML snippet per item.
- HTML styling was updated to match the sample newsletter block (headline/excerpt styles).

### User’s new direction
- Stop creating Mailchimp drafts entirely.
- Stop using Parse.ly.
- Only generate HTML snippets for each WordPress post.
- Run automatically Monday + Thursday mornings.
- Write output to a text file.
- Ideally send the HTML snippets to a Slack channel.

### Open questions to resolve
1) Timezone and exact schedule time for Monday/Thursday runs.
2) How many posts to include each run:
   - all since last run, or
   - a fixed count (e.g., top N recent)?
3) Output file path and naming (e.g., `projects/newsletter/output/newsletter-items-YYYY-MM-DD.html`).
4) Slack delivery method:
   - incoming webhook URL?
   - should it be stored in `.env`?
5) Whether to keep any manual override (e.g., `--since`).

### Next implementation steps (once questions answered)
1) Remove Mailchimp + Parse.ly code paths.
2) Keep WordPress fetch; add a state file to track last run time.
3) Add Slack posting (if webhook provided).
4) Add scheduler (cron/launchd) for Mon/Thu mornings.

### Update (2026-02-02)
- Added `projects/newsletter/generate-newsletter-slack.py`.
  - Pulls WordPress posts since last run (state file) or `--since`.
  - Sorts by Parse.ly returning visitors.
  - Generates one `.html` file containing 10 subject lines + item snippets.
  - Uploads the `.html` file to Slack channel using bot token + channel ID.
- Added `projects/newsletter/run-newsletter-slack.sh` runner script (loads `.env` and runs generator).
- Added `projects/newsletter/install-newsletter-launchd.sh` to install a launchd job for 10:00 AM ET on Mondays/Thursdays.
- Added newsletter automation env vars to `projects/newsletter/.env.example`.
- Added GitHub Actions workflow `.github/workflows/newsletter-slack.yml` for cloud scheduling (Mon/Thu 10:00 AM ET).
- Added setup guide `projects/newsletter/GITHUB_ACTIONS_SETUP.md`.
- Added Slack slash-command prototype in `projects/newsletter-slack-command/` for on-demand single-post HTML (`/newsletter-html <url>`).
