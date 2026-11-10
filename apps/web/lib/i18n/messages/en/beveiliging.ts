import type { nl } from "../beveiliging";

export const en: Record<keyof typeof nl, string> = {
  "guard.adminOnly": "Only an administrator can change this.",
  "lockctl.open": "Released: MediaGuard may replace files in your library (originals go to quarantine first).",
  "lockctl.closed": "Locked: MediaGuard does not replace files in your library.",
  "lockctl.lock": "Lock again",
  "lockctl.lockTitle": "Lock the library again?",
  "lockctl.lockBody": "MediaGuard will no longer replace files in your library until you release it again. Analyses and trial encodes keep running.",
  "lockctl.locked": "Library locked",
  "secrets.unreadable": "Unreadable",
  "secrets.mismatchTitle": "secret.key does not match the database",
  "secrets.mismatchBody": "{count} stored key(s) cannot be decrypted; integrations using them do not work. Put the right secret.key back in the appdata folder (e.g. from your recovery bundle) and restart, or enter the keys again below.",
  "pw.title": "Change password",
  "pw.sub": "Other devices are signed out afterwards; this device stays signed in.",
  "pw.current": "Current password",
  "pw.new": "New password",
  "pw.repeat": "Repeat new password",
  "pw.mismatch": "The new passwords do not match.",
  "pw.save": "Change password",
  "pw.done": "Password changed",
  "sec.nudgeTitle": "Tip: turn on two-step verification.",
  "sec.nudgeBody": "MediaGuard can be reached over the internet. With a code from your phone, a guessed or leaked password is not enough to sign in. It stays your choice.",
};
