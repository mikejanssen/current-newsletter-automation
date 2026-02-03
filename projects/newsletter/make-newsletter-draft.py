#!/usr/bin/env python3
import argparse
import base64
import datetime as dt
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def request_json(method, url, headers=None, payload=None):
    data = None
    req_headers = {"User-Agent": UA}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, resp.headers, body, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, e.headers, body, None
    except Exception as e:
        return None, {}, str(e), None


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
    # Convert straight apostrophes to curly apostrophes.
    text = re.sub(r"(?<=\w)'(?=\w)", "’", text)
    text = text.replace("'", "’")
    # Convert straight double quotes to curly opening/closing quotes.
    out = []
    open_quote = True
    for ch in text:
        if ch == '"':
            out.append("“" if open_quote else "”")
            open_quote = not open_quote
        else:
            out.append(ch)
    return "".join(out)


def parse_iso(dt_str):
    if dt_str and dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    return dt.datetime.fromisoformat(dt_str)


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def get_mailchimp_headers(api_key):
    auth_token = base64.b64encode(f"anystring:{api_key}".encode("utf-8")).decode("utf-8")
    return {"Authorization": f"Basic {auth_token}"}


def get_list_id(mc_base, mc_headers, audience_name):
    status, _, body, data = request_json("GET", f"{mc_base}/lists?count=1000", headers=mc_headers)
    if status != 200 or not isinstance(data, dict):
        raise RuntimeError(f"Mailchimp lists lookup failed status={status}: {body[:200]}")
    for lst in data.get("lists", []):
        if lst.get("name") == audience_name:
            return lst.get("id")
    raise RuntimeError(f"Mailchimp audience not found: {audience_name}")


def get_last_sent_campaign(mc_base, mc_headers, list_id):
    params = urllib.parse.urlencode(
        {
            "status": "sent",
            "list_id": list_id,
            "count": 1,
            "sort_field": "send_time",
            "sort_dir": "DESC",
        }
    )
    status, _, body, data = request_json("GET", f"{mc_base}/campaigns?{params}", headers=mc_headers)
    if status != 200 or not isinstance(data, dict):
        raise RuntimeError(f"Mailchimp campaigns lookup failed status={status}: {body[:200]}")
    campaigns = data.get("campaigns", [])
    return campaigns[0] if campaigns else None


def get_template_sections(mc_base, mc_headers, template_id):
    status, _, body, data = request_json(
        "GET", f"{mc_base}/templates/{template_id}/default-content", headers=mc_headers
    )
    if status != 200 or not isinstance(data, dict):
        return None, f"{status} {body[:200]}"
    sections = data.get("sections", {})
    return sections, None


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
        url = f"{site_url}/wp-json/wp/v2/posts?{params}"
        status, headers, body, data = request_json("GET", url)
        if status == 400 and "rest_post_invalid_page_number" in body:
            break
        if status != 200 or not isinstance(data, list):
            raise RuntimeError(f"WordPress posts fetch failed status={status}: {body[:200]}")
        posts.extend(data)
        total_pages = headers.get("X-WP-TotalPages")
        if total_pages and page >= int(total_pages):
            break
        if not total_pages and len(data) < per_page:
            break
        page += 1
    return posts


def normalize_url(url):
    if not url:
        return None, None
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = parsed.path or ""
        path = path.rstrip("/")
        full = f"{host}{path}"
        return full, path or "/"
    except Exception:
        return None, None


def get_featured_image_info(post):
    embedded = post.get("_embedded", {})
    media = embedded.get("wp:featuredmedia", [])
    if media and isinstance(media, list):
        item = media[0]
        details = item.get("media_details", {})
        return {
            "url": item.get("source_url"),
            "width": details.get("width"),
            "height": details.get("height"),
        }
    return {"url": None, "width": None, "height": None}


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
    url = f"{base}?{urllib.parse.urlencode(params)}"
    status, _, body, data = request_json("GET", url)
    if status != 200 or not isinstance(data, dict) or not data.get("success"):
        raise RuntimeError(f"Parse.ly API failed status={status}: {body[:200]}")
    metrics = {}
    for item in data.get("data", []):
        url = item.get("url")
        if url:
            metrics[url] = item.get("visitors_returning", 0)
    return metrics


def subject_suggestions(headline):
    base = strip_html(headline)
    base = re.sub(r"[\"“”’'()]+", "", base).strip()
    words = base.split()
    short = " ".join(words[:8])
    suggestions = [
        f"Inside: {short}",
        f"Top story: {short}",
        f"Today on Current: {short}",
        f"{short} — and more",
        f"A closer look: {short}",
    ]
    # Avoid exact headline repetition
    suggestions = [s for s in suggestions if s.lower() != base.lower()]
    return suggestions[:6]


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


def build_item_html(item, square_index):
    img_html = ""
    if item["image"]:
        width = item.get("image_width")
        height = item.get("image_height")
        is_square = False
        if isinstance(width, int) and isinstance(height, int):
            is_square = abs(width - height) <= 2
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


def build_html(items):
    parts = []
    square_count = 0
    for item in items:
        parts.append(build_item_html(item, square_count))
        width = item.get("image_width")
        height = item.get("image_height")
        if isinstance(width, int) and isinstance(height, int) and abs(width - height) <= 2:
            square_count += 1
    return "\n".join(parts)


def build_section_items(items):
    sections = {}
    square_count = 0
    for idx, item in enumerate(items, start=1):
        key = f"repeat_{idx}"
        sections[key] = build_item_html(item, square_count)
        width = item.get("image_width")
        height = item.get("image_height")
        if isinstance(width, int) and isinstance(height, int) and abs(width - height) <= 2:
            square_count += 1
    return sections


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", help="Override start time (ISO 8601 with timezone)")
    parser.add_argument("--debug", action="store_true", help="Print debug info")
    parser.add_argument(
        "--output-items",
        help="Write per-item HTML snippets to a file (for manual paste into Mailchimp)",
    )
    args = parser.parse_args()

    wp_site = os.environ.get("WORDPRESS_SITE_URL")
    mc_key = os.environ.get("MAILCHIMP_API_KEY")
    mc_dc = os.environ.get("MAILCHIMP_SERVER_PREFIX")
    mc_template_id = os.environ.get("MAILCHIMP_TEMPLATE_ID")
    if mc_template_id:
        mc_template_id = mc_template_id.strip()
    mc_audience_name = os.environ.get("MAILCHIMP_AUDIENCE_NAME")
    mc_section_main = os.environ.get("MAILCHIMP_TEMPLATE_SECTION_MAIN")
    mc_from = os.environ.get("MAILCHIMP_FROM_NAME")
    mc_reply = os.environ.get("MAILCHIMP_REPLY_TO")
    initial_last_send = os.environ.get("INITIAL_LAST_SEND")
    parsely_key = os.environ.get("PARSELY_API_KEY")
    parsely_secret = os.environ.get("PARSELY_API_SECRET")

    required = {
        "WORDPRESS_SITE_URL": wp_site,
        "MAILCHIMP_API_KEY": mc_key,
        "MAILCHIMP_SERVER_PREFIX": mc_dc,
        "MAILCHIMP_TEMPLATE_ID": mc_template_id,
        "MAILCHIMP_AUDIENCE_NAME": mc_audience_name,
        "PARSELY_API_KEY": parsely_key,
        "PARSELY_API_SECRET": parsely_secret,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        print("[FAIL] Missing required env vars: " + ", ".join(missing))
        return 1

    if not mc_section_main:
        print("[WARN] MAILCHIMP_TEMPLATE_SECTION_MAIN not set. Will use repeat_* sections if available.")

    wp_site = wp_site.rstrip("/")
    mc_base = f"https://{mc_dc}.api.mailchimp.com/3.0"
    mc_headers = get_mailchimp_headers(mc_key)

    list_id = get_list_id(mc_base, mc_headers, mc_audience_name)
    last_campaign = get_last_sent_campaign(mc_base, mc_headers, list_id)

    since_dt = None
    if args.since:
        since_dt = parse_iso(args.since)
        if since_dt.tzinfo is None:
            print("[FAIL] --since must include timezone (e.g., 2025-01-01T09:00:00-05:00).")
            return 1
    elif last_campaign and last_campaign.get("send_time"):
        since_dt = parse_iso(last_campaign["send_time"])
    elif initial_last_send:
        since_dt = parse_iso(initial_last_send)
    else:
        print("[FAIL] No last send time available. Set INITIAL_LAST_SEND in .env or pass --since.")
        return 1

    now_dt = now_utc()
    print(f"[INFO] Pulling posts after {since_dt.isoformat()} and before {now_dt.isoformat()}")

    posts = fetch_wp_posts(wp_site, since_dt, now_dt)
    if not posts:
        print("[WARN] No posts found in this window. Aborting.")
        return 0

    metrics = fetch_parsely_metrics(parsely_key, parsely_secret, since_dt, now_dt)
    items = build_items(posts, metrics)
    if args.debug:
        print(f"[DEBUG] Posts fetched: {len(posts)}")
        print(f"[DEBUG] Items built: {len(items)}")
        print(f"[DEBUG] Lead title: {items[0]['title'] if items else 'N/A'}")
    if args.output_items:
        with open(args.output_items, "w", encoding="utf-8") as f:
            f.write("<!-- Newsletter item snippets -->\n")
            for idx, item in enumerate(items, start=1):
                f.write(f"<!-- Item {idx} -->\n")
                f.write(build_item_html(item, idx - 1))
                f.write("\n\n")
        print(f"[OK] Wrote item snippets to {args.output_items}")
    lead = items[0]
    subject_options = subject_suggestions(lead["title"])

    # Create draft campaign
    if last_campaign and last_campaign.get("settings"):
        settings = last_campaign["settings"]
        from_name = mc_from or settings.get("from_name")
        reply_to = mc_reply or settings.get("reply_to")
    else:
        from_name = mc_from
        reply_to = mc_reply

    if not from_name or not reply_to:
        print("[FAIL] Missing MAILCHIMP_FROM_NAME or MAILCHIMP_REPLY_TO (and no prior campaign to copy).")
        return 1

    title = f"Current Newsletter Draft {now_dt.date().isoformat()}"
    campaign_payload = {
        "type": "regular",
        "recipients": {"list_id": list_id},
        "settings": {
            "title": title,
            "subject_line": "Subject line needed",
            "from_name": from_name,
            "reply_to": reply_to,
            "template_id": int(mc_template_id),
        },
    }

    status, _, body, data = request_json("POST", f"{mc_base}/campaigns", headers=mc_headers, payload=campaign_payload)
    if status not in (200, 201) or not isinstance(data, dict):
        raise RuntimeError(f"Mailchimp campaign create failed status={status}: {body[:200]}")
    campaign_id = data.get("id")

    # Fill template sections: preserve header/footer by starting from defaults
    default_sections, err = get_template_sections(mc_base, mc_headers, mc_template_id)
    if default_sections is None:
        raise RuntimeError(f"Mailchimp template sections fetch failed: {err}")

    sections_payload = dict(default_sections)
    repeat_sections = {k: v for k, v in sections_payload.items() if k.startswith("repeat_")}
    if args.debug:
        print(f"[DEBUG] Template repeat sections: {', '.join(sorted(repeat_sections.keys())) or 'None'}")
    if repeat_sections:
        item_sections = build_section_items(items[: len(repeat_sections)])
        for key in repeat_sections.keys():
            sections_payload[key] = item_sections.get(key, "")
        if args.debug:
            print(
                "[DEBUG] Updated repeat sections: "
                + ", ".join(sorted(item_sections.keys())) if item_sections else "[DEBUG] Updated repeat sections: None"
            )
        if len(items) > len(repeat_sections):
            print(
                f"[WARN] Template has {len(repeat_sections)} repeat blocks; "
                f"{len(items) - len(repeat_sections)} extra posts not inserted."
            )
    else:
        if not mc_section_main:
            raise RuntimeError(
                "No repeat_* sections found and MAILCHIMP_TEMPLATE_SECTION_MAIN is not set."
            )
        html_block = build_html(items)
        sections_payload[mc_section_main] = html_block

    content_payload = {
        "template": {
            "id": int(mc_template_id),
            "sections": sections_payload,
        }
    }
    status, _, body, _ = request_json(
        "PUT", f"{mc_base}/campaigns/{campaign_id}/content", headers=mc_headers, payload=content_payload
    )
    if status not in (200, 204):
        raise RuntimeError(f"Mailchimp content update failed status={status}: {body[:200]}")

    if args.debug:
        status, _, body, data = request_json(
            "GET", f"{mc_base}/campaigns/{campaign_id}/content", headers=mc_headers
        )
        if status == 200 and isinstance(data, dict):
            print(f"[DEBUG] Content fetch ok. Keys: {', '.join(sorted(data.keys()))}")
            sections = (data.get("template") or {}).get("sections") or {}
            sample = sections.get("repeat_1") or sections.get(mc_section_main)
            print(f"[DEBUG] Sections returned: {len(sections)}")
            if sample:
                sample_line = sample.strip().replace("\n", " ")[:200]
                print(f"[DEBUG] Section sample: {sample_line}...")
            else:
                print("[DEBUG] Section sample: None")
            html_body = data.get("html")
            if isinstance(html_body, str):
                print(f"[DEBUG] HTML length: {len(html_body)}")
        else:
            print(f"[DEBUG] Content fetch failed status={status}: {body[:200]}")

    print("[OK] Draft campaign created")
    print(f"[INFO] Campaign ID: {campaign_id}")
    print("[INFO] Subject line suggestions:")
    for s in subject_options:
        print(f"- {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
