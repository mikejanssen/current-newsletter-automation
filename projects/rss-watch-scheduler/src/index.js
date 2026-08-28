const SCHEDULES = {
  "35 12 * * 1-5": { mode: "morning", offsets: [-4] },
  "5 13 * * 1-5": { mode: "morning", offsets: [-4] },
  "35 13 * * 1-5": { mode: "morning", offsets: [-4, -5] },
  "5 14 * * 1-5": { mode: "morning", offsets: [-5] },
  "35 14 * * 1-5": { mode: "morning", offsets: [-5] },
  "12 18 * * 1-5": { mode: "update", offsets: [-4] },
  "42 18 * * 1-5": { mode: "update", offsets: [-4] },
  "12 19 * * 1-5": { mode: "update", offsets: [-4, -5] },
  "42 19 * * 1-5": { mode: "update", offsets: [-5] },
  "12 20 * * 1-5": { mode: "update", offsets: [-5] },
};

function newYorkParts(date) {
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "numeric",
    hourCycle: "h23",
    timeZoneName: "longOffset",
  });
  const parts = Object.fromEntries(
    formatter.formatToParts(date).map(({ type, value }) => [type, value]),
  );
  const offsetMatch = parts.timeZoneName.match(/^GMT([+-])(\d{2}):?(\d{2})$/);
  if (!offsetMatch) {
    throw new Error(`Could not parse New York UTC offset: ${parts.timeZoneName}`);
  }
  const sign = offsetMatch[1] === "+" ? 1 : -1;
  const offsetHours = sign * (
    Number(offsetMatch[2]) + Number(offsetMatch[3]) / 60
  );
  return { hour: Number(parts.hour), offsetHours };
}

export function selectDelivery(cron, scheduledTime) {
  const candidate = SCHEDULES[cron];
  if (!candidate) {
    return null;
  }

  const { hour, offsetHours } = newYorkParts(new Date(scheduledTime));
  const withinWindow = (
    candidate.mode === "morning" && hour >= 7 && hour < 12
  ) || (
    candidate.mode === "update" && hour >= 13 && hour < 18
  );
  if (!candidate.offsets.includes(offsetHours) || !withinWindow) {
    return null;
  }
  return candidate.mode;
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
