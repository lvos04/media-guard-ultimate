import { redirect } from "next/navigation";

// Oude AV1-route (vóór 0.2.3): MediaGuard regelt bibliotheken zelf
export default function Page() {
  redirect("/av1/instellingen");
}
