#!/usr/bin/env python3
import base64
import json
import os
import sys
import urllib.parse
import urllib.request


def fetch_json(url, headers=None):
    merged_headers = {
        # Some endpoints (e.g., WP REST behind WAF) block unknown agents
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    }
    if headers:
        merged_headers.update(headers)
    req = urllib.request.Request(url, headers=merged_headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, body, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body, None
    except Exception as e:
        return None, str(e), None


def ok(msg):
    print(f"[OK] {msg}")


def warn(msg):
    print(f"[WARN] {msg}")


def fail(msg):
    print(f"[FAIL] {msg}")


def main():
    missing = []
    wp_site = os.environ.get("WORDPRESS_SITE_URL")
    mc_key = os.environ.get("MAILCHIMP_API_KEY")
    mc_dc = os.environ.get("MAILCHIMP_SERVER_PREFIX")
    mc_template_id = os.environ.get("MAILCHIMP_TEMPLATE_ID")
    mc_audience_name = os.environ.get("MAILCHIMP_AUDIENCE_NAME")
    parsely_key = os.environ.get("PARSELY_API_KEY")
    parsely_secret = os.environ.get("PARSELY_API_SECRET")

    for name, val in [
        ("WORDPRESS_SITE_URL", wp_site),
        ("MAILCHIMP_API_KEY", mc_key),
        ("MAILCHIMP_SERVER_PREFIX", mc_dc),
        ("MAILCHIMP_TEMPLATE_ID", mc_template_id),
        ("MAILCHIMP_AUDIENCE_NAME", mc_audience_name),
        ("PARSELY_API_KEY", parsely_key),
        ("PARSELY_API_SECRET", parsely_secret),
    ]:
        if not val:
            missing.append(name)

    if missing:
        fail("Missing required env vars: " + ", ".join(missing))
        print("Load your .env first: source ./load-env.sh")
        return 1

    # WordPress REST check
    wp_site = wp_site.rstrip("/")
    wp_url = f"{wp_site}/wp-json/wp/v2/posts?per_page=1"
    status, body, data = fetch_json(wp_url)
    if status == 200 and isinstance(data, list):
        ok(f"WordPress REST reachable ({wp_url})")
    else:
        fail(f"WordPress REST failed ({wp_url}) status={status}")
        warn(body[:500])

    # Parse.ly API check (returning visitors metric)
    parsely_base = "https://api.parsely.com/v2/analytics/posts"
    parsely_params = {
        "apikey": parsely_key,
        "secret": parsely_secret,
        "limit": 1,
        "sort": "visitors_returning",
    }
    parsely_url = f"{parsely_base}?{urllib.parse.urlencode(parsely_params)}"
    status, body, data = fetch_json(parsely_url)
    if status == 200 and isinstance(data, dict) and data.get("success") is True:
        ok("Parse.ly API reachable (analytics/posts)")
    else:
        fail(f"Parse.ly API failed status={status}")
        warn(body[:500])

    # Mailchimp API check
    mc_base = f"https://{mc_dc}.api.mailchimp.com/3.0"
    auth_token = base64.b64encode(f"anystring:{mc_key}".encode("utf-8")).decode("utf-8")
    mc_headers = {"Authorization": f"Basic {auth_token}"}

    status, body, data = fetch_json(f"{mc_base}/ping", headers=mc_headers)
    if status == 200:
        ok("Mailchimp API ping ok")
    else:
        fail(f"Mailchimp ping failed status={status}")
        warn(body[:500])

    # Mailchimp template check
    status, body, data = fetch_json(f"{mc_base}/templates/{mc_template_id}", headers=mc_headers)
    if status == 200 and isinstance(data, dict):
        ok(f"Mailchimp template found (id={mc_template_id})")
    else:
        fail(f"Mailchimp template lookup failed id={mc_template_id} status={status}")
        warn(body[:500])

    # Mailchimp audience check
    lists_url = f"{mc_base}/lists?count=1000"
    status, body, data = fetch_json(lists_url, headers=mc_headers)
    if status == 200 and isinstance(data, dict):
        lists = data.get("lists", [])
        match = next((lst for lst in lists if lst.get("name") == mc_audience_name), None)
        if match:
            ok(f"Mailchimp audience found: {mc_audience_name}")
        else:
            fail(f"Mailchimp audience not found: {mc_audience_name}")
            warn("Check the audience name or reduce count/offset if you have >1000 audiences.")
    else:
        fail(f"Mailchimp audiences lookup failed status={status}")
        warn(body[:500])

    return 0


if __name__ == "__main__":
    sys.exit(main())
