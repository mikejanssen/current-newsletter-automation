const MORNING_CRON = "5,35 12-14 * * 1-5";
const UPDATE_CRON = "12,42 18-20 * * 1-5";

const DELIVERY_TIMES = {
  morning: new Set(["8:35", "9:05", "9:35"]),
  update: new Set(["14:12", "14:42", "15:12"]),
};

function newYorkParts(date) {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "numeric",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const parts = Object.fromEntries(
    formatter.formatToParts(date).map(({ type, value }) => [type, value]),
  );
  return { hour: Number(parts.hour), minute: Number(parts.minute) };
}

export function selectDelivery(cron, scheduledTime) {
  const mode = cron === MORNING_CRON
    ? "morning"
    : cron === UPDATE_CRON
      ? "update"
      : null;
  if (!mode) {
    return null;
  }

  const { hour, minute } = newYorkParts(new Date(scheduledTime));
  return DELIVERY_TIMES[mode].has(`${hour}:${String(minute).padStart(2, "0")}`)
    ? mode
    : null;
}

export async function dispatchWorkflow(env, mode, fetchImpl = fetch) {
  const owner = encodeURIComponent(env.GITHUB_OWNER);
  const repo = encodeURIComponent(env.GITHUB_REPO);
  const workflow = encodeURIComponent(env.GITHUB_WORKFLOW);
  const response = await fetchImpl(
    `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${env.GITHUB_ACTIONS_TOKEN}`,
        "User-Agent": "current-rss-watch-scheduler",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: {
          mode,
          dry_run: "false",
          scheduled_delivery: "true",
        },
      }),
    },
  );
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`GitHub workflow dispatch failed (${response.status}): ${detail}`);
  }
}

export default {
  async scheduled(controller, env) {
    const mode = selectDelivery(controller.cron, controller.scheduledTime);
    if (!mode) {
      console.log(`Skipping inactive Eastern-time trigger: ${controller.cron}`);
      return;
    }
    await dispatchWorkflow(env, mode);
    console.log(`Dispatched ${mode} RSS Watch workflow`);
  },
};
