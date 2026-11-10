"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { StateView } from "@/components/mg/states";

/** 'Gevorderd' is opgegaan in Instellingen: de sectie Gereedschap. Oude links komen daar uit. */
export default function AdvancedRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace("/instellingen#gereedschap"); }, [router]);
  return <StateView kind="loading" />;
}
