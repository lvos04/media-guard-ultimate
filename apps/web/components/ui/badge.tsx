// Peil Badge-familie (ONTWERPTAAL §4.2–4.5, COMPONENTEN C-03/C-08): één vorm per as, altijd icoon + woord.
//   gezondheid → pil (rounded-full), 2 px rand; Gezond zonder vlak; Onbekend gestippeld; Verouderd gestippeld + arcering
//   label      → neutrale pil (geen statusbetekenis); "strong" met sterke rand (bv. Ontbreekt)
// De hookklasse `chip` (en `chip--<toon>`) blijft op elke pil (CSS-haken en tests).
import { cva, type VariantProps } from "class-variance-authority";

export const badgeVariants = cva(
  "chip inline-flex w-fit max-w-full min-h-6 items-center gap-1.5 rounded-full border-2 px-2 py-0.5 text-sm font-semibold leading-tight break-words align-middle [&_svg]:size-[1.125rem] [&_svg]:shrink-0",
  {
    variants: {
      tone: {
        healthy: "chip--healthy border-transparent bg-transparent px-0.5 text-healthy-ink [&_svg]:text-healthy",
        success: "chip--success border-transparent bg-muted text-foreground",
        warning: "chip--warning border-degraded bg-degraded-bg text-degraded-ink",
        danger: "chip--danger border-down bg-down-bg text-down-ink",
        info: "chip--info border-transparent bg-info-bg text-info-ink",
        accent: "chip--accent border-transparent bg-muted text-foreground",
        neutral: "border-transparent bg-muted text-foreground",
        strong: "chip--strong border-border-strong bg-transparent text-foreground",
        unknown: "chip--unknown border-dashed border-unknown bg-unknown-bg text-unknown-ink",
        unlinked: "chip--unlinked border-dashed border-unknown bg-unknown-bg text-unknown-ink",
        stale: "chip--stale arcering border-dashed border-stale text-unknown-ink",
        paused: "chip--paused border-paused bg-paused-bg text-paused-ink",
        linked: "chip--linked border-border-strong bg-transparent text-foreground",
      },
      size: { standaard: "", groot: "chip--lg px-2.5 py-1 text-base" },
      outline: { true: "chip--outline border border-border-strong bg-transparent", false: "" },
    },
    defaultVariants: { tone: "neutral", size: "standaard", outline: false },
  },
);
export type BadgeVariantProps = VariantProps<typeof badgeVariants>;
