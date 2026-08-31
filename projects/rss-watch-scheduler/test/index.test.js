import assert from "node:assert/strict";
import test from "node:test";

import { dispatchWorkflow, selectDelivery } from "../src/index.js";

test("selects EDT morning and update deliveries", () => {
  assert.equal(selectDelivery("5,35 12-14 * * 2-6", Date.parse("2026-08-28T12:35:00Z")), "morning");
  assert.equal(selectDelivery("12,42 18-20 * * 2-6", Date.parse("2026-08-28T18:12:00Z")), "update");
});

test("selects EST morning and update deliveries", () => {
  assert.equal(selectDelivery("5,35 12-14 * * 2-6", Date.parse("2026-12-04T13:35:00Z")), "morning");
  assert.equal(selectDelivery("12,42 18-20 * * 2-6", Date.parse("2026-12-04T19:12:00Z")), "update");
});

test("skips the inactive daylight-saving counterpart", () => {
  assert.equal(selectDelivery("5,35 12-14 * * 2-6", Date.parse("2026-08-28T12:05:00Z")), null);
  assert.equal(selectDelivery("5,35 12-14 * * 2-6", Date.parse("2026-12-04T12:35:00Z")), null);
  assert.equal(selectDelivery("12,42 18-20 * * 2-6", Date.parse("2026-08-28T20:42:00Z")), null);
});

test("dispatches a protected scheduled delivery", async () => {
  let request;
  const fakeFetch = async (url, options) => {
    request = { url, options };
    return new Response(null, { status: 204 });
  };
  await dispatchWorkflow({
    GITHUB_OWNER: "mikejanssen",
    GITHUB_REPO: "current-newsletter-automation",
    GITHUB_WORKFLOW: "rss-watch.yml",
    GITHUB_ACTIONS_TOKEN: "test-token",
  }, "morning", fakeFetch);

  assert.equal(
    request.url,
    "https://api.github.com/repos/mikejanssen/current-newsletter-automation/actions/workflows/rss-watch.yml/dispatches",
  );
  assert.equal(request.options.method, "POST");
  assert.deepEqual(JSON.parse(request.options.body), {
    ref: "main",
    inputs: {
      mode: "morning",
      dry_run: "false",
      scheduled_delivery: "true",
    },
  });
});
