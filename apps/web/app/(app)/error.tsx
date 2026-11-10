"use client";

import { ErrorFallback } from "@/components/mg/error-boundary";

export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <ErrorFallback page error={error} onRetry={reset} />;
}
