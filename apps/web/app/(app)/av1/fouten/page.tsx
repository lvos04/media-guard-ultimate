import { redirect } from "next/navigation";

// Oude AV1-route (vóór 0.2.3): fouten zijn een filter in de wachtrij
export default function Page() {
  redirect("/av1/wachtrij?status=mislukt");
}
