#!/usr/bin/env python3
import base64
import json
import os
import sys
import urllib.request


def fetch_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8")
        return resp.status, json.loads(body)


def main():
    mc_key = os.environ.get("MAILCHIMP_API_KEY")
    mc_dc = os.environ.get("MAILCHIMP_SERVER_PREFIX")
    if not mc_key or not mc_dc:
        print("[FAIL] Missing MAILCHIMP_API_KEY or MAILCHIMP_SERVER_PREFIX")
        print("Load your .env first: source ./load-env.sh")
        return 1

    mc_base = f"https://{mc_dc}.api.mailchimp.com/3.0"
    auth_token = base64.b64encode(f"anystring:{mc_key}".encode("utf-8")).decode("utf-8")
    mc_headers = {"Authorization": f"Basic {auth_token}"}

    status, data = fetch_json(f"{mc_base}/templates?count=1000", headers=mc_headers)
    if status != 200:
        print(f"[FAIL] Mailchimp templates lookup failed status={status}")
        return 1

    templates = data.get("templates", [])
    if not templates:
        print("[WARN] No templates found.")
        return 0

    print("id\tname\ttype")
    for t in templates:
        print(f"{t.get('id')}\t{t.get('name')}\t{t.get('type')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
