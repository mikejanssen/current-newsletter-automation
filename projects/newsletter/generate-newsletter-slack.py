#!/usr/bin/env python3
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def request_json(method, url, headers=None, payload=None):
    data = None
    req_headers = {"User-Agent": UA}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, resp.headers, body, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, e.headers, body, None
    except Exception as e:
        return None, {}, str(e), None


def request_bytes(method, url, headers=None, payload=None):
    req_headers = {"User-Agent": UA}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=payload, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            return resp.status, resp.headers, body
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def parse_iso(dt_str):
    if dt_str and dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return dt.datetime.fromisoformat(dt_str)


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return html.unescape(text)


def truncate(text, max_len=220):
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1].rsplit(" ", 1)[0]
    return cut + "…"


def smarten_punctuation(text):
    if not text:
        return text
    text = re.sub(r"(?<=\w)'(?=\w)", "’", text)
    text = text.replace("'", "’")
    out = []
    open_quote = True
    for ch in text:
        if ch == '"':
            out.append("“" if open_quote else "”")
            open_quote = not open_quote
        else:
            out.append(ch)
    return "".join(out)


def normalize_url(url):
    if not url:
        return None, None
    parsed = urllib.parse.urlparse(url)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/")
    full = f"{host}{path}"
    return full, path or "/"


def fetch_wp_posts(site_url, after_dt, before_dt):
    posts = []
    page = 1
    per_page = 100
    after = after_dt.isoformat()
    before = before_dt.isoformat()
    while True:
        params = urllib.parse.urlencode(
            {
                "after": after,
                "before": before,
                "per_page": per_page,
                "page": page,
                "_embed": 1,
                "orderby": "date",
                "order": "asc",
            }
        )
        url = f"{site_url.rstrip('/')}/wp-json/wp/v2/posts?{params}"
        status, headers, body, data = request_json("GET", url)
        if status == 400 and "rest_post_invalid_page_number" in body:
            break
        if status != 200 or not isinstance(data, list):
            raise RuntimeError(f"WordPress posts fetch failed status={status}: {body[:220]}")
        posts.extend(data)
        total_pages = headers.get("X-WP-TotalPages")
        if total_pages and page >= int(total_pages):
            break
        if not total_pages and len(data) < per_page:
            break
        page += 1
    return posts


def fetch_parsely_metrics(api_key, api_secret, start_dt, end_dt):
    base = "https://api.parsely.com/v2/analytics/posts"
    params = {
        "apikey": api_key,
        "secret": api_secret,
        "period_start": start_dt.strftime("%Y-%m-%d"),
        "period_end": end_dt.strftime("%Y-%m-%d"),
        "limit": 500,
        "sort": "visitors_returning",
    }
    status, _, body, data = request_json("GET", f"{base}?{urllib.parse.urlencode(params)}")
    if status != 200 or not isinstance(data, dict) or not data.get("success"):
        raise RuntimeError(f"Parse.ly API failed status={status}: {body[:220]}")
    metrics = {}
    for item in data.get("data", []):
        item_url = item.get("url") or item.get("link")
        val = ((item.get("metrics") or {}).get("visitors_returning"))
        if val is None:
            val = item.get("visitors_returning", 0)
        if item_url:
            metrics[item_url] = int(val or 0)
    return metrics


def coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def pick_best_media_size(details):
    sizes = (details or {}).get("sizes", {})
    best = None
    best_area = -1
    for info in sizes.values():
        if not isinstance(info, dict):
            continue
        url = info.get("source_url")
        width = coerce_int(info.get("width"))
        height = coerce_int(info.get("height"))
        if not url:
            continue
        area = (width or 0) * (height or 0)
        if area > best_area:
            best = {"url": url, "width": width, "height": height}
            best_area = area
    return best


def first_content_image_url(post):
    rendered = ((post.get("content") or {}).get("rendered")) or ""
    m = re.search(r"<img[^>]+src=[\"']([^\"']+)[\"']", rendered, flags=re.IGNORECASE)
    return m.group(1) if m else None


def get_featured_image_info(post):
    embedded = post.get("_embedded", {})
    media = embedded.get("wp:featuredmedia", [])
    if media and isinstance(media, list):
        item = media[0]
        details = item.get("media_details", {})
        width = coerce_int(details.get("width"))
        height = coerce_int(details.get("height"))
        source_url = item.get("source_url")
        if not source_url:
            best_size = pick_best_media_size(details)
            if best_size:
                source_url = best_size["url"]
                if width is None:
                    width = best_size["width"]
                if height is None:
                    height = best_size["height"]
        if source_url:
            return {
                "url": source_url,
                "width": width,
                "height": height,
            }

    jetpack_url = post.get("jetpack_featured_media_url")
    if jetpack_url:
        rtt = post.get("rttpg_featured_image_url") or {}
        full = rtt.get("full")
        width = coerce_int(full[1]) if isinstance(full, list) and len(full) > 2 else None
        height = coerce_int(full[2]) if isinstance(full, list) and len(full) > 2 else None
        return {"url": jetpack_url, "width": width, "height": height}

    rtt = post.get("rttpg_featured_image_url") or {}
    full = rtt.get("full")
    if isinstance(full, list) and full and full[0]:
        return {
            "url": full[0],
            "width": coerce_int(full[1]) if len(full) > 2 else None,
            "height": coerce_int(full[2]) if len(full) > 2 else None,
        }

    parsely_meta = ((post.get("parsely") or {}).get("meta") or {})
    parsely_image = parsely_meta.get("image") or {}
    if isinstance(parsely_image, dict) and parsely_image.get("url"):
        return {"url": parsely_image.get("url"), "width": None, "height": None}

    yoast = post.get("yoast_head_json") or {}
    og_images = yoast.get("og_image") or []
    if isinstance(og_images, list) and og_images:
        img = og_images[0] if isinstance(og_images[0], dict) else {}
        if img.get("url"):
            return {
                "url": img.get("url"),
                "width": coerce_int(img.get("width")),
                "height": coerce_int(img.get("height")),
            }
    inline_url = first_content_image_url(post)
    if inline_url:
        return {"url": inline_url, "width": None, "height": None}
    return {"url": None, "width": None, "height": None}


def build_items(posts, metrics):
    metrics_by_full = {}
    metrics_by_path = {}
    for url, value in metrics.items():
        full, path = normalize_url(url)
        if full:
            metrics_by_full[full] = value
        if path:
            metrics_by_path[path] = value

    items = []
    for post in posts:
        link = post.get("link")
        full, path = normalize_url(link)
        returning = 0
        if full and full in metrics_by_full:
            returning = metrics_by_full[full]
        elif path and path in metrics_by_path:
            returning = metrics_by_path[path]

        image = get_featured_image_info(post)
        items.append(
            {
                "title": strip_html(post.get("title", {}).get("rendered", "")),
                "excerpt": truncate(strip_html(post.get("excerpt", {}).get("rendered", ""))),
                "url": link,
                "image": image.get("url"),
                "image_width": image.get("width"),
                "image_height": image.get("height"),
                "date_gmt": post.get("date_gmt"),
                "returning_users": returning,
            }
        )
    items.sort(key=lambda x: (x["returning_users"], x["date_gmt"] or ""), reverse=True)
    return items


def clamp_words(text, max_words):
    words = [w for w in text.split() if w]
    return " ".join(words[:max_words]).strip()


TRAILING_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "to",
    "of",
    "in",
    "on",
    "at",
    "for",
    "from",
    "with",
    "by",
    "about",
    "as",
}


def trim_trailing_stopwords(text):
    words = [w for w in text.split() if w]
    while words and words[-1].lower() in TRAILING_STOPWORDS:
        words.pop()
    return " ".join(words).strip()


def pick_subject_base(headline):
    return smarten_punctuation(strip_html(headline)).strip()


def build_item_html(item, square_index):
    img_html = ""
    if item["image"]:
        width = item.get("image_width")
        height = item.get("image_height")
        is_square = isinstance(width, int) and isinstance(height, int) and abs(width - height) <= 2
        if is_square:
            float_right = (square_index % 2) == 0
            if float_right:
                img_style = "float:right;width:150px;height:150px;margin:0 0 10px 10px;"
            else:
                img_style = "float:left;width:150px;height:150px;margin:0 10px 10px 0;"
            img_html = (
                f"<a href=\"{item['url']}\" target=\"_blank\">"
                f"<img src=\"{item['image']}\" alt=\"\" style=\"{img_style}\" width=\"150\" height=\"150\" /></a>"
            )
        else:
            img_html = (
                f"<a href=\"{item['url']}\" target=\"_blank\">"
                f"<img src=\"{item['image']}\" alt=\"\" "
                f"style=\"border:0px; width:550px; height:309px; margin:0 0 10px;\" "
                f"width=\"550\" height=\"309\" /></a>"
            )

    title_text = html.escape(smarten_punctuation(item["title"]), quote=False)
    excerpt_text = html.escape(smarten_punctuation(item["excerpt"]), quote=False)
    title_html = (
        "<h2 class=\"null\" style=\"display:block;margin:0;padding:0;color:#202020;"
        "font-family:'Open Sans', 'Helvetica Neue', Helvetica, Arial, sans-serif;"
        "font-size:24px;font-style:normal;font-weight:bold;line-height:125%;"
        "letter-spacing:normal;text-align:left;\">"
        f"<a href=\"{item['url']}\" target=\"_blank\">{title_text}</a></h2>"
    )
    excerpt_html = (
        "<p style=\"margin:10px 0;padding:0;mso-line-height-rule:exactly;"
        "-ms-text-size-adjust:100%;-webkit-text-size-adjust:100%;color:#202020;"
        "font-family:Georgia, Times, 'Times New Roman', serif;font-size:18px;"
        "line-height:150%;text-align:left;\">"
        f"{excerpt_text}</p>"
    )
    return f"{img_html}\n\n{title_html}\n\n{excerpt_html}"


def build_output_html(items, since_dt, now_dt):
    square_count = 0
    lines = []
    lines.append(
        "<!-- Newsletter item snippets (ordered by Parse.ly returning visitors) -->"
    )
    lines.append(
        f"<!-- Window: {since_dt.isoformat()} to {now_dt.isoformat()} -->"
    )
    for idx, item in enumerate(items, start=1):
        lines.append(f"<!-- Item {idx} | returning_users={item['returning_users']} -->")
        lines.append(build_item_html(item, square_count))
        lines.append("")
        width = item.get("image_width")
        height = item.get("image_height")
        if isinstance(width, int) and isinstance(height, int) and abs(width - height) <= 2:
            square_count += 1
    return "\n".join(lines).strip() + "\n"


def load_state(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(path, state):
    state_dir = os.path.dirname(path)
    if state_dir:
        os.makedirs(state_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def choose_default_since(timezone_name, now_dt):
    tz = ZoneInfo(timezone_name)
    now_local = now_dt.astimezone(tz)
    anchor = now_local.replace(hour=14, minute=0, second=0, microsecond=0)
    # Roll backward to the most recent Mon/Thu 2:00 PM local cutoff.
    while anchor >= now_local or anchor.weekday() not in (0, 3):
        anchor -= dt.timedelta(days=1)
        anchor = anchor.replace(hour=14, minute=0, second=0, microsecond=0)
    return anchor.astimezone(dt.timezone.utc)


def choose_since(args, state, timezone_name, now_dt):
    if args.since:
        since = parse_iso(args.since)
        if since.tzinfo is None:
            raise RuntimeError("--since must include timezone.")
        return since.astimezone(dt.timezone.utc)
    if os.environ.get("NEWSLETTER_WINDOW_MODE", "").lower() == "state":
        last_success = state.get("last_successful_run")
        if last_success:
            return parse_iso(last_success).astimezone(dt.timezone.utc)
    initial_since = os.environ.get("NEWSLETTER_INITIAL_SINCE")
    if initial_since:
        return parse_iso(initial_since).astimezone(dt.timezone.utc)
    return choose_default_since(timezone_name, now_dt)


def slack_auth_headers(bot_token):
    return {"Authorization": f"Bearer {bot_token}"}


def slack_upload_html(bot_token, channel_id, file_path, initial_comment=None):
    with open(file_path, "rb") as f:
        file_bytes = f.read()
    filename = os.path.basename(file_path)

    # Step 1: request upload URL
    params = urllib.parse.urlencode(
        {
            "filename": filename,
            "length": str(len(file_bytes)),
        }
    )
    # request_json sends JSON payloads only, so use a form request here.
    req = urllib.request.Request(
        "https://slack.com/api/files.getUploadURLExternal",
        data=params.encode("utf-8"),
        headers={**slack_auth_headers(bot_token), "Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        j = json.loads(resp.read().decode("utf-8"))
    if not j.get("ok"):
        raise RuntimeError(f"Slack getUploadURLExternal failed: {j}")
    upload_url = j["upload_url"]
    file_id = j["file_id"]

    # Step 2: upload bytes to the pre-signed URL
    status, _, upload_body = request_bytes("POST", upload_url, payload=file_bytes)
    if status not in (200, 201):
        msg = upload_body.decode("utf-8", errors="replace")
        raise RuntimeError(f"Slack upload URL POST failed status={status}: {msg[:220]}")

    # Step 3: complete upload and share into channel
    complete_payload = {
        "files": [{"id": file_id, "title": filename}],
        "channel_id": channel_id,
    }
    if initial_comment:
        complete_payload["initial_comment"] = initial_comment
    status, _, body, data = request_json(
        "POST",
        "https://slack.com/api/files.completeUploadExternal",
        headers=slack_auth_headers(bot_token),
        payload=complete_payload,
    )
    if status != 200 or not isinstance(data, dict) or not data.get("ok"):
        raise RuntimeError(f"Slack completeUploadExternal failed status={status}: {body[:220]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", help="Override start time (ISO 8601 with timezone)")
    parser.add_argument("--output-file", help="Optional explicit output file path")
    parser.add_argument("--state-file", help="Optional explicit state file path")
    parser.add_argument("--skip-slack", action="store_true", help="Generate file but do not send to Slack")
    parser.add_argument("--dry-run", action="store_true", help="Do not update state file")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    wp_site = os.environ.get("WORDPRESS_SITE_URL")
    parsely_key = os.environ.get("PARSELY_API_KEY")
    parsely_secret = os.environ.get("PARSELY_API_SECRET")
    timezone_name = os.environ.get("NEWSLETTER_TIMEZONE", "America/New_York")
    output_dir = os.environ.get("NEWSLETTER_OUTPUT_DIR", "output")
    state_file = args.state_file or os.environ.get("NEWSLETTER_STATE_FILE", "output/newsletter-state.json")
    slack_token = os.environ.get("SLACK_BOT_TOKEN")
    slack_channel_id = os.environ.get("SLACK_CHANNEL_ID")

    required = {
        "WORDPRESS_SITE_URL": wp_site,
        "PARSELY_API_KEY": parsely_key,
        "PARSELY_API_SECRET": parsely_secret,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError("Missing required env vars: " + ", ".join(missing))
    if not args.skip_slack:
        if not slack_token or not slack_channel_id:
            raise RuntimeError("Need SLACK_BOT_TOKEN and SLACK_CHANNEL_ID unless --skip-slack is used.")

    now_dt = now_utc()
    state = load_state(state_file)
    since_dt = choose_since(args, state, timezone_name, now_dt)
    if since_dt >= now_dt:
        raise RuntimeError(f"Since time {since_dt.isoformat()} is not before now.")
    if args.debug:
        print(f"[DEBUG] Pulling posts from {since_dt.isoformat()} to {now_dt.isoformat()}")

    posts = fetch_wp_posts(wp_site, since_dt, now_dt)
    if not posts:
        print("[INFO] No posts found in this window.")
        if not args.dry_run:
            state["last_successful_run"] = now_dt.isoformat()
            save_state(state_file, state)
        return 0

    metrics = fetch_parsely_metrics(parsely_key, parsely_secret, since_dt, now_dt)
    items = build_items(posts, metrics)
    html_out = build_output_html(items, since_dt, now_dt)

    if args.output_file:
        out_file = args.output_file
    else:
        os.makedirs(output_dir, exist_ok=True)
        date_label = now_dt.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d")
        out_file = os.path.join(output_dir, f"newsletter-items-{date_label}.html")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"[OK] Wrote {out_file}")

    if not args.skip_slack:
        comment = (
            f"Newsletter HTML ({since_dt.astimezone(ZoneInfo(timezone_name)).strftime('%b %d %I:%M %p %Z')} "
            f"to {now_dt.astimezone(ZoneInfo(timezone_name)).strftime('%b %d %I:%M %p %Z')})"
        )
        slack_upload_html(slack_token, slack_channel_id, out_file, initial_comment=comment)
        print(f"[OK] Uploaded file to Slack channel {slack_channel_id}")

    if not args.dry_run:
        state["last_successful_run"] = now_dt.isoformat()
        save_state(state_file, state)
        if args.debug:
            print(f"[DEBUG] Updated state file: {state_file}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[FAIL] {e}")
        sys.exit(1)
