"use client";

import { SimpleSettings } from "@/components/mg/simple-settings";
import { ErrorState, StateView } from "@/components/mg/states";
import { ApiError } from "@/lib/api/client";
import type { SettingsSchema } from "@/lib/api/contracts";
import { useResource } from "@/lib/api/hooks";

/** 'Systeem & prestaties' (CPU-limiet, aantal encodes, venster) onder de tab Systeem (M15/H11). */
export function SystemPerformance() {
  const schema = useResource<SettingsSchema>("/settings/schema");
  if (schema.loading && !schema.data) return <StateView kind="loading" compact />;
  if (!schema.data) return <ErrorState compact error={schema.error ?? new ApiError("server", 0, "", "/settings/schema")} onRetry={() => void schema.reload()} />;
  return <SimpleSettings schema={schema.data} only={["prestaties"]} />;
}
