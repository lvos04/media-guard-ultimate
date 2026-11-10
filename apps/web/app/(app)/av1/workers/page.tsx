import { redirect } from "next/navigation";

// Oude AV1-route (vóór 0.2.3): workers staan nu op het overzicht, instellingen onder Instellingen
export default function Page() {
  redirect("/av1/instellingen#workers");
}
