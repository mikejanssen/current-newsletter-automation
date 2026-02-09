# Slack `/newsletter-html` Command

This adds a Slack slash command so anyone in your newsroom channel can paste a Current post URL and get back a newsletter HTML file.

## What it does
- Slash command: `/newsletter-html https://current.org/...`
- Validates Slack request signature.
- Fetches that WordPress post.
- Builds newsletter-style HTML snippet.
- Uploads an `.html` file into the same Slack channel.

## 1) Deploy on Cloudflare Workers
From `projects/newsletter-slack-command`:

```bash
npm create cloudflare@latest .
# If prompted, keep existing files, deploy a Worker
```

Or, if Wrangler is already installed:

```bash
wrangler login
wrangler deploy
```

Copy the worker URL (example: `https://newsletter-slack-command.<subdomain>.workers.dev`).

## 2) Configure Slack app
In Slack app settings:

1. **OAuth & Permissions -> Bot Token Scopes**
   - `commands`
   - `files:write`
   - `chat:write`
2. Reinstall app to workspace.
3. **Slash Commands -> Create New Command**
   - Command: `/newsletter-html`
   - Request URL: your Worker URL
   - Short description: "Generate newsletter HTML for one post URL"
4. Invite bot to target channel(s): `/invite @YourBotName`

## 3) Set Worker secrets
Set these in Cloudflare:

```bash
wrangler secret put SLACK_BOT_TOKEN
wrangler secret put SLACK_SIGNING_SECRET
wrangler secret put WORDPRESS_SITE_URL
```

Use:
- `SLACK_BOT_TOKEN`: Slack Bot User OAuth token (`xoxb-...`)
- `SLACK_SIGNING_SECRET`: from Slack app -> Basic Information
- `WORDPRESS_SITE_URL`: usually `https://current.org`

Then redeploy:

```bash
wrangler deploy
```

## 4) Test
In Slack channel:

```text
/newsletter-html https://current.org/2026/02/kcur-stays-on-air-after-quick-move-from-longtime-studios/
```

You should get an uploaded `.html` file in that channel.
