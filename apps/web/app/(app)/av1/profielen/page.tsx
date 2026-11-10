import { redirect } from "next/navigation";

// Oude AV1-route (vóór 0.2.3): profielen staan onder Instellingen
export default function Page() {
  redirect("/av1/instellingen#profielen");
}
