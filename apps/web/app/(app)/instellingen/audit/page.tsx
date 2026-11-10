import { redirect } from "next/navigation";

// De audit staat op één plek: Activiteit → Geschiedenis → Audit.
export default function AuditRedirect() {
  redirect("/activiteit?tab=geschiedenis&view=audit");
}
