"use client";

import { OctagonX, RotateCcw } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";

import { useI18n } from "@/lib/i18n";

/** Nederlandse melding in plaats van het standaard (Engelse) foutscherm van Next.js. */
export function ErrorFallback({ error, onRetry, page = false }: { error?: unknown; onRetry?: () => void; page?: boolean }) {
  const { t } = useI18n();
  const detail = error instanceof Error ? `${error.name}: ${error.message}` : error ? String(error) : "";
  return (
    <div className={page ? "state" : "state state--compact"} role="alert" data-testid="error-boundary">
      <span className="state-icon border-down bg-down-bg text-down-ink"><OctagonX className="ico" aria-hidden="true" /></span>
      <div className="state-title">{page ? t("err.pageTitle") : t("err.panelTitle")}</div>
      <div className="state-text">{t("err.body")}</div>
      {detail ? <div className="state-meta"><details className="tech-details"><summary>{t("state.techDetails")}</summary><span className="mono" style={{ overflowWrap: "anywhere" }}>{detail}</span></details></div> : null}
      {onRetry ? <button type="button" className="btn btn--sm" onClick={onRetry}><RotateCcw className="ico" aria-hidden="true" />{t("action.retry")}</button> : null}
    </div>
  );
}

/** Foutgrens per paneel: één kapot onderdeel neemt de rest van de pagina niet meer mee. */
export class ErrorBoundary extends Component<{ children?: ReactNode; page?: boolean; resetKey?: unknown }, { error: unknown; key: unknown }> {
  state: { error: unknown; key: unknown } = { error: null, key: this.props.resetKey };

  static getDerivedStateFromError(error: unknown) {
    return { error };
  }

  static getDerivedStateFromProps(props: { resetKey?: unknown }, state: { error: unknown; key: unknown }) {
    // nieuwe pagina of nieuwe gegevens: opnieuw proberen
    if (props.resetKey !== state.key) return { error: null, key: props.resetKey };
    return null;
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("MediaGuard: onderdeel kon niet worden getoond", error, info.componentStack);
  }

  render() {
    if (this.state.error) return <ErrorFallback error={this.state.error} page={this.props.page} onRetry={() => this.setState({ error: null })} />;
    return this.props.children ?? null;
  }
}
