# GitHub Actions Setup (Newsletter HTML to Slack)

This sets up automated newsletter HTML generation and Slack delivery every Monday/Thursday at 10:00 AM ET.

## 1) Put this project in a private GitHub repo
- Create a private repo.
- Push this folder structure, including `.github/workflows/newsletter-slack.yml`.

## 2) Add repository secrets
In GitHub: **Repo -> Settings -> Secrets and variables -> Actions -> New repository secret**

Add:
- `WORDPRESS_SITE_URL` (example: `https://current.org`)
- `PARSELY_API_KEY`
- `PARSELY_API_SECRET`
- `SLACK_BOT_TOKEN`
- `SLACK_CHANNEL_ID` (channel ID like `C...` or `G...`)

## 3) Slack app requirements
- Bot token must have scopes:
  - `files:write`
  - `chat:write`
- Reinstall app after adding scopes.
- Invite bot to target channel.

## 4) Enable GitHub Actions
- In repo: **Actions** tab -> enable workflows if prompted.
- Open workflow **Newsletter HTML to Slack**.

## 5) Test manually
- Click **Run workflow**.
- Optional:
  - `since`: explicit ISO timestamp with timezone
  - `skip_slack`: true to test without posting

## 6) Scheduled runs
- Schedule is in `.github/workflows/newsletter-slack.yml`.
- It is configured for Mondays/Thursdays at 10:00 AM ET (DST-aware via UTC + time gate).

## 7) Troubleshooting
- If no Slack file appears:
  - verify bot scopes and channel invite
  - verify `SLACK_CHANNEL_ID`
  - check workflow run logs in Actions tab
- If Parse.ly/WordPress calls fail:
  - verify corresponding secrets are set correctly
