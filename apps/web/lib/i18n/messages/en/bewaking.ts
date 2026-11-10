import type { nl } from "../bewaking";

export const en: Record<keyof typeof nl, string> = {
  "attention.dismissedCount": "{count} item(s) hidden as a deliberate choice",
  "attention.dismissedSub": "They come back on their own when you change the setting.",
  "attention.restore": "Show again",
  "attention.restored": "Hidden items are back in the list",
  "system.tasks": "Background tasks",
  "system.tasksSub": "What MediaGuard does on its own, and when it last succeeded.",
  "system.taskLastOk": "Last succeeded {when}",
  "system.taskNeverOk": "Not succeeded since start",
  "system.taskEvent": "Runs in the background, without a fixed round",
  "system.taskError": "Last error: {error}",
  "system.taskRestarts": "Restarted {count}×",
  "system.taskOk": "Running",
  "system.taskStale": "Idle",
  "system.taskFailing": "Failing",
  "system.monitorLast": "Monitoring: last round {when}",
  "system.monitorOff": "Background monitoring is off",
  "system.monitorNever": "Monitoring: no round yet since start (the first follows in about 3 minutes)",
  "system.monitorRun": "Check now",
  "system.monitorDone": "Monitoring: everything checked",
};
