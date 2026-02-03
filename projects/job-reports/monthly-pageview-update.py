#!/usr/bin/env python3
import argparse
import base64
import datetime as dt
import json
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


DEFAULT_URLS = [
    "https://current.org/job-description-tool/",
    "https://current.org/job-description-depot/",
]

GA_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GA4_RUN_REPORT_URL = "https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"

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
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, body, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body, None


def month_window(timezone_name, month_override=None):
    tz = ZoneInfo(timezone_name)
    if month_override:
        year, month = [int(x) for x in month_override.split("-", 1)]
        start = dt.datetime(year, month, 1, tzinfo=tz)
    else:
        now = dt.datetime.now(tz)
        this_month_start = dt.datetime(now.year, now.month, 1, tzinfo=tz)
        last_day_prev_month = this_month_start - dt.timedelta(days=1)
        start = dt.datetime(last_day_prev_month.year, last_day_prev_month.month, 1, tzinfo=tz)
    if start.month == 12:
        next_start = dt.datetime(start.year + 1, 1, 1, tzinfo=tz)
    else:
        next_start = dt.datetime(start.year, start.month + 1, 1, tzinfo=tz)
    end = next_start - dt.timedelta(days=1)
    return start.date(), end.date()


def b64url(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def sign_rs256_with_openssl(message_bytes, private_key_pem):
    with tempfile.NamedTemporaryFile("w", delete=False) as key_file:
        key_file.write(private_key_pem)
        key_path = key_file.name
    try:
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_path],
            input=message_bytes,
            capture_output=True,
            check=True,
        )
        return proc.stdout
    finally:
        os.remove(key_path)


def load_service_account(path):
    with open(path, "r", encoding="utf-8") as f:
        info = json.load(f)
    required = ["client_email", "private_key"]
    missing = [k for k in required if not info.get(k)]
    if missing:
        raise RuntimeError(f"Service account JSON missing keys: {', '.join(missing)}")
    return info


def fetch_oauth_access_token(service_account_info):
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    header = {"alg": "RS256", "typ": "JWT"}
    claim_set = {
        "iss": service_account_info["client_email"],
        "scope": GA_SCOPE,
        "aud": GOOGLE_TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    }
    signing_input = (
        f"{b64url(json.dumps(header, separators=(',', ':')).encode('utf-8'))}."
        f"{b64url(json.dumps(claim_set, separators=(',', ':')).encode('utf-8'))}"
    )
    signature = sign_rs256_with_openssl(signing_input.encode("utf-8"), service_account_info["private_key"])
    assertion = f"{signing_input}.{b64url(signature)}"
    form = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OAuth token request failed: status={e.code} body={body[:200]}") from e
    token = data.get("access_token")
    if not token:
        raise RuntimeError("OAuth token response missing access_token.")
    return token


def url_path(page_url):
    parsed = urllib.parse.urlparse((page_url or "").strip())
    path = parsed.path or "/"
    return path if path.startswith("/") else f"/{path}"


def fetch_views(access_token, property_id, period_start, period_end, page_url):
    endpoint = GA4_RUN_REPORT_URL.format(property_id=property_id)
    path = url_path(page_url)
    payload = {
        "dateRanges": [
            {
                "startDate": period_start.isoformat(),
                "endDate": period_end.isoformat(),
            }
        ],
        "dimensions": [{"name": "pagePath"}],
        "metrics": [{"name": "screenPageViews"}],
        "dimensionFilter": {
            "filter": {
                "fieldName": "pagePath",
                "stringFilter": {
                    "matchType": "EXACT",
                    "value": path,
                    "caseSensitive": False,
                },
            }
        },
        "limit": "1",
    }
    status, body, data = request_json(
        "POST",
        endpoint,
        headers={"Authorization": f"Bearer {access_token}"},
        payload=payload,
    )
    if status != 200 or not isinstance(data, dict):
        raise RuntimeError(f"GA4 runReport failed for {page_url}: status={status} body={body[:240]}")

    rows = data.get("rows") or []
    if not rows:
        return 0
    metric_values = rows[0].get("metricValues") or []
    if not metric_values:
        return 0
    raw = metric_values[0].get("value", "0")
    try:
        return int(float(raw))
    except ValueError:
        return 0


def build_message(period_start, period_end, timezone_name, rows):
    month_label = period_start.strftime("%B %Y")
    date_label = (
        f"{period_start.strftime('%b')} {period_start.day}-"
        f"{period_end.strftime('%b')} {period_end.day}, {period_end.year}"
    )
    lines = [
        f"Monthly pageview update ({month_label}, {date_label} {timezone_name}, GA4)",
    ]
    for page_url, views in rows:
        lines.append(f"- {page_url}: {views:,} views")
    return "\n".join(lines)


def post_to_slack(webhook_url, message):
    status, body, _ = request_json("POST", webhook_url, payload={"text": message})
    if status not in (200, 201):
        raise RuntimeError(f"Slack webhook failed: status={status} body={body[:200]}")


def main():
    parser = argparse.ArgumentParser(description="Send monthly GA4 pageview update.")
    parser.add_argument(
        "--timezone",
        default="America/New_York",
        help="IANA timezone used for month boundaries (default: America/New_York).",
    )
    parser.add_argument(
        "--url",
        action="append",
        dest="urls",
        help="Page URL to include. Pass multiple times. Defaults to the two job description pages.",
    )
    parser.add_argument(
        "--month",
        help="Month override in YYYY-MM format. Default is previous month in the selected timezone.",
    )
    parser.add_argument(
        "--output-file",
        help="Optional path to write the message text.",
    )
    parser.add_argument(
        "--post-slack",
        action="store_true",
        help="Post message to Slack using SLACK_WEBHOOK_URL.",
    )
    parser.add_argument(
        "--ga4-property-id",
        default=os.environ.get("GA4_PROPERTY_ID"),
        help="GA4 property ID. Defaults to GA4_PROPERTY_ID env var.",
    )
    parser.add_argument(
        "--ga-service-account-json",
        default=os.environ.get("GA_SERVICE_ACCOUNT_JSON"),
        help="Path to Google service account JSON. Defaults to GA_SERVICE_ACCOUNT_JSON env var.",
    )
    args = parser.parse_args()

    if not args.ga4_property_id:
        raise RuntimeError("Missing GA4 property ID. Set --ga4-property-id or GA4_PROPERTY_ID.")
    if not args.ga_service_account_json:
        raise RuntimeError("Missing service account path. Set --ga-service-account-json or GA_SERVICE_ACCOUNT_JSON.")

    slack_webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if args.post_slack and not slack_webhook:
        raise RuntimeError("Missing SLACK_WEBHOOK_URL for --post-slack.")

    urls = args.urls or DEFAULT_URLS
    period_start, period_end = month_window(args.timezone, args.month)
    service_account_info = load_service_account(args.ga_service_account_json)
    access_token = fetch_oauth_access_token(service_account_info)
    rows = []
    for page_url in urls:
        rows.append((page_url, fetch_views(access_token, args.ga4_property_id, period_start, period_end, page_url)))

    message = build_message(period_start, period_end, args.timezone, rows)
    print(message)

    if args.output_file:
        with open(args.output_file, "w", encoding="utf-8") as f:
            f.write(message + "\n")
        print(f"[OK] Wrote report to {args.output_file}")

    if args.post_slack:
        post_to_slack(slack_webhook, message)
        print("[OK] Posted to Slack.")


if __name__ == "__main__":
    main()
