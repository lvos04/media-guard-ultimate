"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Oud adres: audio- en ondertitelvoorkeuren staan nu in Instellingen (Taal & anime). */
export default function AudioRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace("/instellingen?view=all&q=audio"); }, [router]);
  return null;
}
