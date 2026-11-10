import type { nl } from "../onderhoud";

export const en: Record<keyof typeof nl, string> = {
  "system.backupPreUpdate": "before update",
  "system.backupStaged": "Ready to be restored",
  "system.restorePreUpdateBody": "This backup is from before an update and also contains the AV1 engine database (if listed). It is not restored right away: MediaGuard prepares it and swaps the databases as soon as the app stops. Then stop the app in TrueNAS, pick the previous version if needed and start it again. The current databases are kept. Type the backup name to confirm.",
  "system.restorePreUpdate": "Prepare",
  "system.staged": "Prepared: stop the app in TrueNAS to restore",
  "jobs.logsTruncated": "Showing only the last 2,000 lines.",
};
