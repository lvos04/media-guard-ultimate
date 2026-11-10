import type { nl } from "../recovery";

export const en: Record<keyof typeof nl, string> = {
  "rec.title": "Recovered",
  "rec.sub": "Completed downloads Sonarr/Radarr never imported (e.g. because of a missing SABnzbd category). MediaGuard imports them when it is clear where they belong; nothing is ever deleted.",
  "rec.summary": "{imported} imported after all · {pending} in progress · {failed} not recoverable",
  "rec.lastRun": "last checked {when}",
  "rec.empty": "Nothing to recover: all completed downloads were imported.",
  "rec.imported": "Imported after all",
  "rec.importing": "Offered to Sonarr/Radarr",
  "rec.notRecoverable": "Not recoverable",
  "rec.error": "Will retry",
  "rec.fromTrash": "restored from the trash",
  "rec.pathIssues": "{n} download folder(s) not recognised; they are skipped.",
};
