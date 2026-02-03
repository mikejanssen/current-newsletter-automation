# Newsletter Automation Spec (Draft)

## Goal
Automate production of the twice‑weekly Current newsletter by pulling new WordPress posts, ordering by returning‑visitor popularity (Parse.ly), and generating a Mailchimp draft from the existing template. Human review remains required before send; ads and external links are added manually.

## Systems & Credentials
- WordPress: public REST API at `https://current.org/wp-json/wp/v2/`
- Parse.ly: API access available (credentials needed)
- Mailchimp: API key available; template ID `10003132`
- Audience/List: `Current Newsletter`
  - Draft target time: 10:00 AM ET Mondays and Thursdays

## Data Inputs
### WordPress post fields
From `wp-json/wp/v2/posts`:
- `id`
- `date_gmt`
- `link`
- `title.rendered`
- `excerpt.rendered`
- `featured_media` (ID)

From `wp-json/wp/v2/media/{id}`:
- `source_url` (featured image URL)
- `alt_text`

### Parse.ly (ordering)
- Endpoint: use Parse.ly API to retrieve posts by URL for the date range
- Metric: `returning_users` (posts sorted descending)
- Fallback: if Parse.ly fails or missing data, sort by `date_gmt` descending

### Mailchimp
- Create a draft campaign using template ID `10003132`
- Populate content blocks with post items
- Leave placeholders for:
  - Ads
  - “Other publications” links

## Workflow (Draft)
1) Determine newsletter window
   - From last send time (stored locally or read from last campaign send time) to “now”.
2) Pull WordPress posts for window
   - `GET /wp-json/wp/v2/posts?after=...&before=...&per_page=100&_embed=1`
   - If `_embed` is available and includes featured media, skip separate media calls.
3) Pull Parse.ly metrics for URLs in window
   - Build map: `post_url -> returning_users`
   - Order posts by `returning_users` desc
4) Build newsletter items
   - `headline = title.rendered` (strip HTML)
   - `excerpt = excerpt.rendered` (strip HTML, keep short)
   - `image_url = featured image` (from `_embed` or media endpoint)
   - `url = link`
5) Create Mailchimp draft campaign
   - Audience: `Current Newsletter`
   - Template ID: `7558439`
   - Content: insert items into template sections
   - Ads/Other links: insert placeholder blocks
6) Subject line suggestions
   - Generate 3–6 options from lead item
   - Rule: avoid close overlap with the exact headline phrasing
   - Provide to editor in draft notes or as a separate field
7) Human review in Mailchimp
   - Editor rearranges order if desired
   - Adds ads and external links
   - Picks subject line and sends

## Open Questions / Next Inputs
- Parse.ly API credentials (key + secret, or token)
- Mailchimp API key
- How to identify “last send time” (manual input or query last campaign send time)
- Mailchimp template block names/IDs to map content into
- Bootstrapping: initial “last send time” needed once (e.g., 2026-01-26 2:00 PM ET)

## Notes
- If WordPress public REST has rate limits or missing fields, switch to authenticated access via application password.
- If Parse.ly API access is delayed, use date sorting until metrics are wired.
