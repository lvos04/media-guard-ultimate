"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

/** Oud adres: staat nu onder Instellingen → Koppelingen (één plek om te koppelen en te testen). */
export default function SeerrRedirect() {
  return <Suspense><Go /></Suspense>;
}

function Go() {
  const router = useRouter();
  const params = useSearchParams();
  useEffect(() => {
    const qs = new URLSearchParams(params.toString());
    qs.set("tab", "seerr");
    router.replace(`/inrichten?${qs.toString()}${window.location.hash}`);
  }, [params, router]);
  return null;
}
