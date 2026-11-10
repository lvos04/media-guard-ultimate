"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

/** Oud adres: de takengeschiedenis staat nu onder Activiteit → Geschiedenis. */
export default function JobsRedirect() {
  return <Suspense><Redirect /></Suspense>;
}

function Redirect() {
  const router = useRouter();
  const params = useSearchParams();
  useEffect(() => {
    const qs = new URLSearchParams(params.toString());
    qs.set("tab", qs.get("status") === "failed" ? "problemen" : "geschiedenis");
    router.replace(`/activiteit?${qs.toString()}`);
  }, [params, router]);
  return null;
}
