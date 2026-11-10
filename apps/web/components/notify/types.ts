export type ChannelKind = "ntfy" | "pushover" | "telegram" | "discord" | "slack" | "webhook" | "email";
export type Choice = "direct" | "digest" | "never";

export interface NotifyChannel {
  id: number;
  kind: ChannelKind;
  kind_nl: string;
  name: string;
  enabled: boolean;
  auto_created: boolean;
  last_ok_at: string | null;
  last_error: string | null;
  created_at: string;
  config: Record<string, string | number>;
  secrets_set: Record<string, boolean>;
  subscribe_url?: string;
  qr_svg?: string | null;
}

export interface NotifyOverview {
  channels: NotifyChannel[];
  kinds: Array<{ kind: ChannelKind; label_nl: string; fields: Array<{ name: string; secret: boolean; required: boolean }> }>;
  categories: Array<{ key: string; label_nl: string; value: Choice; default: Choice }>;
  choices: Array<{ value: Choice; label_nl: string }>;
  digest: { daily: boolean; time: string; weekly: boolean; weekday: number; timezone: string };
  rate_per_hour: number;
  base_url: string;
  storage?: { daily: boolean; weekly: boolean; growth_alert_gb: number };
}

export interface DigestStats {
  encoded: number; saved_bytes: number; delivered: number; errors: number; waiting: number;
  items: Array<{ category: string; category_nl: string; severity: string; title: string; count: number }>;
  body_nl?: string;
}

export interface DigestRow { id: number; kind: "daily" | "weekly"; period_start: string; period_end: string; text_nl: string; stats: DigestStats; created_at: string }
