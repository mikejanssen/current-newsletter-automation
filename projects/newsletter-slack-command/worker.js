export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    const rawBody = await request.text();
    const authError = await verifySlackSignature(request, rawBody, env.SLACK_SIGNING_SECRET);
    if (authError) {
      return new Response(authError, { status: 401 });
    }

    const form = new URLSearchParams(rawBody);
    const text = (form.get("text") || "").trim();
    const channelId = form.get("channel_id");
    const userId = form.get("user_id");

    if (!channelId) {
      return new Response("Missing channel_id.", { status: 400 });
    }
    if (!text) {
      return new Response("Usage: /newsletter-html https://current.org/2026/.../", { status: 200 });
    }

    const postUrl = normalizeSlackUrl(text);
    if (!postUrl) {
      return new Response("Please provide one valid URL.", { status: 200 });
    }

    ctx.waitUntil(
      processCommand({
        env,
        postUrl,
        channelId,
        requestedBy: userId || "unknown",
      })
    );

    return new Response("Got it - generating HTML and uploading a file now.", {
      status: 200,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  },
};

async function processCommand({ env, postUrl, channelId, requestedBy }) {
  try {
    const item = await fetchPostItem(postUrl, env.WORDPRESS_SITE_URL || "https://current.org");
    const snippet = buildItemHtml(item, 0);
    const fileText = [
      "<!-- Newsletter HTML snippet for single post -->",
      `<!-- Requested by: ${requestedBy} -->`,
      `<!-- Source URL: ${postUrl} -->`,
      "",
      snippet,
      "",
    ].join("\n");

    const slug = postUrl.replace(/\/+$/, "").split("/").pop() || "newsletter-item";
    const filename = `newsletter-item-${slug}.html`;
    const initialComment = `Generated newsletter HTML for ${postUrl}`;

    await uploadFileToSlack({
      botToken: env.SLACK_BOT_TOKEN,
      channelId,
      filename,
      content: fileText,
      initialComment,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    if (env.SLACK_BOT_TOKEN) {
      await postSlackMessage(env.SLACK_BOT_TOKEN, channelId, `Failed to generate HTML: ${message}`);
    }
  }
}

function normalizeSlackUrl(input) {
  const cleaned = input.replace(/[<>]/g, "").split(/\s+/)[0];
  try {
    const u = new URL(cleaned);
    if (!/^https?:$/.test(u.protocol)) return null;
    return u.toString();
  } catch {
    return null;
  }
}

async function verifySlackSignature(request, rawBody, signingSecret) {
  if (!signingSecret) return "SLACK_SIGNING_SECRET is not configured.";

  const timestamp = request.headers.get("x-slack-request-timestamp");
  const signature = request.headers.get("x-slack-signature");
  if (!timestamp || !signature) return "Missing Slack signature headers.";

  const ts = Number(timestamp);
  if (!Number.isFinite(ts)) return "Invalid Slack timestamp.";
  const now = Math.floor(Date.now() / 1000);
  if (Math.abs(now - ts) > 60 * 5) return "Stale Slack request.";

  const base = `v0:${timestamp}:${rawBody}`;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(signingSecret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sigBytes = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(base));
  const expected = `v0=${toHex(sigBytes)}`;
  return safeEqual(expected, signature) ? null : "Invalid Slack signature.";
}

function toHex(buf) {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let out = 0;
  for (let i = 0; i < a.length; i += 1) out |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return out === 0;
}

async function fetchPostItem(postUrl, wordpressSiteUrl) {
  const requested = new URL(postUrl);
  const wpHost = new URL(wordpressSiteUrl);
  if (requested.host !== wpHost.host) {
    throw new Error(`URL must be on ${wpHost.host}`);
  }

  const slug = requested.pathname.replace(/\/+$/, "").split("/").pop();
  if (!slug) throw new Error("Could not determine post slug from URL.");

  const endpoint = `${wpHost.origin}/wp-json/wp/v2/posts?slug=${encodeURIComponent(slug)}&_embed=1&per_page=1`;
  const resp = await fetch(endpoint, { headers: { "User-Agent": "newsletter-slack-command" } });
  if (!resp.ok) throw new Error(`WordPress fetch failed (${resp.status}).`);

  const posts = await resp.json();
  if (!Array.isArray(posts) || posts.length === 0) throw new Error("Post not found.");
  const post = posts[0];

  const media = (((post || {})._embedded || {})["wp:featuredmedia"] || [])[0] || {};
  const details = media.media_details || {};

  return {
    title: stripHtml((post.title || {}).rendered || ""),
    excerpt: truncate(stripHtml((post.excerpt || {}).rendered || ""), 220),
    url: (post.link || postUrl).trim(),
    image: media.source_url || null,
    imageWidth: typeof details.width === "number" ? details.width : null,
    imageHeight: typeof details.height === "number" ? details.height : null,
  };
}

function stripHtml(text) {
  return decodeEntities(
    String(text || "")
      .replace(/<[^>]+>/g, " ")
      .replace(/\s+/g, " ")
      .trim()
  );
}

function decodeEntities(text) {
  return text
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#x27;/gi, "'")
    .replace(/&nbsp;/g, " ")
    .replace(/&rsquo;/g, "’")
    .replace(/&lsquo;/g, "‘")
    .replace(/&ldquo;/g, "“")
    .replace(/&rdquo;/g, "”");
}

function truncate(text, maxLen) {
  if (text.length <= maxLen) return text;
  const cut = text.slice(0, maxLen - 1).replace(/\s+\S*$/, "");
  return `${cut}...`;
}

function smartenPunctuation(text) {
  let out = String(text || "");
  out = out.replace(/(?<=\w)'(?=\w)/g, "’");
  out = out.replace(/'/g, "’");

  const chars = [];
  let open = true;
  for (const ch of out) {
    if (ch === '"') {
      chars.push(open ? "“" : "”");
      open = !open;
    } else {
      chars.push(ch);
    }
  }
  return chars.join("");
}

function escapeHtml(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function buildItemHtml(item, squareIndex) {
  let imgHtml = "";
  if (item.image) {
    const isSquare =
      Number.isInteger(item.imageWidth) &&
      Number.isInteger(item.imageHeight) &&
      Math.abs(item.imageWidth - item.imageHeight) <= 2;
    if (isSquare) {
      const floatRight = squareIndex % 2 === 0;
      const imgStyle = floatRight
        ? "float:right;width:150px;height:150px;margin:0 0 10px 10px;"
        : "float:left;width:150px;height:150px;margin:0 10px 10px 0;";
      imgHtml = `<a href="${item.url}" target="_blank"><img src="${item.image}" alt="" style="${imgStyle}" width="150" height="150" /></a>`;
    } else {
      imgHtml = `<a href="${item.url}" target="_blank"><img src="${item.image}" alt="" style="border:0px; width:550px; height:309px; margin:0 0 10px;" width="550" height="309" /></a>`;
    }
  }

  const titleText = escapeHtml(smartenPunctuation(item.title));
  const excerptText = escapeHtml(smartenPunctuation(item.excerpt));
  const titleHtml =
    `<h2 class="null" style="display:block;margin:0;padding:0;color:#202020;` +
    `font-family:'Open Sans', 'Helvetica Neue', Helvetica, Arial, sans-serif;` +
    `font-size:24px;font-style:normal;font-weight:bold;line-height:125%;` +
    `letter-spacing:normal;text-align:left;">` +
    `<a href="${item.url}" target="_blank">${titleText}</a></h2>`;
  const excerptHtml =
    `<p style="margin:10px 0;padding:0;mso-line-height-rule:exactly;` +
    `-ms-text-size-adjust:100%;-webkit-text-size-adjust:100%;color:#202020;` +
    `font-family:Georgia, Times, 'Times New Roman', serif;font-size:18px;` +
    `line-height:150%;text-align:left;">${excerptText}</p>`;

  return `${imgHtml}\n\n${titleHtml}\n\n${excerptHtml}`;
}

async function uploadFileToSlack({ botToken, channelId, filename, content, initialComment }) {
  if (!botToken) throw new Error("SLACK_BOT_TOKEN is not configured.");
  const bytes = new TextEncoder().encode(content);

  const form = new URLSearchParams({
    filename,
    length: String(bytes.byteLength),
  });
  const getUrlResp = await fetch("https://slack.com/api/files.getUploadURLExternal", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${botToken}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: form.toString(),
  });
  const getUrlJson = await getUrlResp.json();
  if (!getUrlJson.ok) throw new Error(`Slack getUploadURLExternal failed: ${getUrlJson.error || "unknown_error"}`);

  const uploadResp = await fetch(getUrlJson.upload_url, {
    method: "POST",
    headers: { "Content-Type": "text/html; charset=utf-8" },
    body: bytes,
  });
  if (!uploadResp.ok) throw new Error(`Slack file upload failed (${uploadResp.status}).`);

  const completeResp = await fetch("https://slack.com/api/files.completeUploadExternal", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${botToken}`,
      "Content-Type": "application/json; charset=utf-8",
    },
    body: JSON.stringify({
      files: [{ id: getUrlJson.file_id, title: filename }],
      channel_id: channelId,
      initial_comment: initialComment,
    }),
  });
  const completeJson = await completeResp.json();
  if (!completeJson.ok) throw new Error(`Slack completeUploadExternal failed: ${completeJson.error || "unknown_error"}`);
}

async function postSlackMessage(botToken, channelId, text) {
  await fetch("https://slack.com/api/chat.postMessage", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${botToken}`,
      "Content-Type": "application/json; charset=utf-8",
    },
    body: JSON.stringify({
      channel: channelId,
      text,
    }),
  });
}
