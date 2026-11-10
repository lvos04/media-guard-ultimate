import type { nl } from "../r034";

export const en: Record<keyof typeof nl, string> = {
  "details.notMeasured": "not measured",
  "details.langUnknown": "language unknown",
  "detail.wasSize": "was {size}",
  "av1.lane.notStarting": "{count} job(s) waiting, but none is starting:",
  "av1.lane.notStartingUnknown": "the engine reports no reason yet; MediaGuard retries next round.",
  "delivery.lastFailure": "Last download failure: {reason}",
  "overview.queuedEncodes": "and {count} encode(s) queued",
  "av1.q.verifiedOf": "{passed} of the last {total} checks passed",
  "audit.undoGone": "No longer possible: original was cleaned up",
  "history.by": "started by {who}",
  "wanted.nextRound": "Waiting for next search round",
  "av1.kpi.forecastHours": "current plan done in ± {n} hours",
  "activity.recent.external": "By another app",
};
