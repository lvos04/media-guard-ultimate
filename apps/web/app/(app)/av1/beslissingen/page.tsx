import { redirect } from "next/navigation";

// MediaGuard beslist zelf welke bestanden worden omgezet (Migratie & optimalisatie); de engine
// voert alleen uit. Beslissingen staan daarom niet meer in AV1 Studio: de uitvoering zie je in
// de wachtrij.
export default function Page() {
  redirect("/av1/wachtrij");
}
