import { redirect } from "next/navigation";

// Oude AV1-route (vóór 0.2.3): de AI-adviseur van MediaGuard vervangt de AI-canary
export default function Page() {
  redirect("/ai");
}
