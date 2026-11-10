"use client";

import { BookOpen, Workflow } from "lucide-react";

import { Card, PageHeader } from "@/components/mg/primitives";
import { glossary, TERM_ORDER, type TermKey } from "@/lib/glossary";
import { useI18n, type MessageKey } from "@/lib/i18n";

/** Stappen van aanvraag tot opgeruimd; `term` koppelt aan de woordenlijst. */
const STEPS: Array<{ label: MessageKey; sub: MessageKey; term?: TermKey }> = [
  { label: "help.step.request", sub: "help.step.requestSub", term: "aanvraag" },
  { label: "help.step.search", sub: "help.step.searchSub", term: "backfill" },
  { label: "help.step.download", sub: "help.step.downloadSub" },
  { label: "help.step.import", sub: "help.step.importSub" },
  { label: "help.step.check", sub: "help.step.checkSub", term: "identiteit" },
  { label: "help.step.av1", sub: "help.step.av1Sub", term: "baan" },
  { label: "help.step.release", sub: "help.step.releaseSub", term: "vrijgavecontrole" },
  { label: "help.step.handoff", sub: "help.step.handoffSub", term: "overdracht" },
  { label: "help.step.cleanup", sub: "help.step.cleanupSub", term: "prullenbak" },
];

/**
 * 'Hoe werkt MediaGuard' (rapport 5, M10): één schema van aanvraag tot opgeruimd, plus een
 * woordenlijst met ankers waar de <Term>-uitleg op de pagina's naar verwijst.
 */
export default function HowItWorksPage() {
  const { t, locale } = useI18n();
  const g = glossary(locale);
  return (
    <>
      <PageHeader title={t("help.title")} sub={t("help.sub")} />
      <div className="stack" style={{ display: "grid", gap: 16 }}>
        <Card title={t("help.flowTitle")} icon={Workflow} sub={t("help.flowSub")}>
          <FlowWide />
          <FlowNarrow />
          <p className="hint" style={{ marginTop: 12 }}>{t("help.flowNote")}</p>
        </Card>
        <Card title={t("help.glossaryTitle")} icon={BookOpen}>
          <dl className="glossary" data-testid="glossary">
            {TERM_ORDER.map((k) => (
              <div key={k}>
                <dt id={k}>{g[k].term}</dt>
                <dd>{g[k].long}</dd>
              </div>
            ))}
          </dl>
        </Card>
      </div>
    </>
  );
}

const W = 132, H = 58, GAP = 30;

/** Desktop: twee rijen (slang), lees van links naar rechts en dan terug. */
function FlowWide() {
  const { t } = useI18n();
  const row1 = STEPS.slice(0, 5), row2 = STEPS.slice(5);
  const x = (i: number) => 10 + i * (W + GAP);
  const y1 = 10, y2 = 120;
  return (
    <svg className="flow-svg flow-wide" viewBox={`0 0 ${x(5) - GAP + 10} 190`} role="img" aria-label={t("help.flowTitle")}>
      <Arrow id="flow-arrow-breed" />
      {row1.map((s, i) => <Node key={s.label} x={x(i)} y={y1} label={t(s.label)} sub={t(s.sub)} keyStep={!!s.term} />)}
      {row1.slice(0, -1).map((_, i) => <line key={`a${i}`} className="edge" x1={x(i) + W} y1={y1 + H / 2} x2={x(i + 1) - 4} y2={y1 + H / 2} />)}
      <path className="edge" d={`M ${x(4) + W / 2} ${y1 + H} V ${y2 - 4}`} />
      {row2.map((s, i) => <Node key={s.label} x={x(4 - i)} y={y2} label={t(s.label)} sub={t(s.sub)} keyStep={!!s.term} />)}
      {row2.slice(0, -1).map((_, i) => <line key={`b${i}`} className="edge" x1={x(4 - i)} y1={y2 + H / 2} x2={x(3 - i) + W + 4} y2={y2 + H / 2} />)}
    </svg>
  );
}

/** Telefoon: één kolom van boven naar beneden. */
function FlowNarrow() {
  const { t } = useI18n();
  const step = H + 22;
  return (
    <svg className="flow-svg flow-narrow" viewBox={`0 0 300 ${STEPS.length * step}`} role="img" aria-label={t("help.flowTitle")}>
      <Arrow id="flow-arrow-smal" />
      {STEPS.map((s, i) => <Node key={s.label} x={20} y={4 + i * step} w={260} label={t(s.label)} sub={t(s.sub)} keyStep={!!s.term} />)}
      {STEPS.slice(0, -1).map((_, i) => <line key={i} className="edge" x1={150} y1={4 + i * step + H} x2={150} y2={4 + (i + 1) * step - 4} />)}
    </svg>
  );
}

/** Eén pijlpunt per schema: twee keer hetzelfde id (breed + smal staan allebei in de DOM) is ongeldige HTML. */
function Arrow({ id }: { id: string }) {
  return (
    <defs>
      <marker id={id} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10 z" />
      </marker>
    </defs>
  );
}

function Node({ x, y, w = W, label, sub, keyStep }: { x: number; y: number; w?: number; label: string; sub: string; keyStep: boolean }) {
  return (
    <g className={`node${keyStep ? " key" : ""}`}>
      <rect x={x} y={y} width={w} height={H} rx={10} />
      <text x={x + w / 2} y={y + 24} textAnchor="middle" fontWeight={600}>{label}</text>
      <text className="sub" x={x + w / 2} y={y + 42} textAnchor="middle">{sub}</text>
    </g>
  );
}
