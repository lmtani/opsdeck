// The installed app's icon badge (dock/taskbar): the sum of what each view
// reports, so GitHub's new events and the logs' new entries add up.

const counts = {};

export function setBadgeCount(view, count) {
  counts[view] = count;
  if (!("setAppBadge" in navigator)) return;
  const total = Object.values(counts).reduce((sum, value) => sum + value, 0);
  const update = total ? navigator.setAppBadge(total) : navigator.clearAppBadge();
  update.catch(() => {
    /* not installed or not supported */
  });
}
