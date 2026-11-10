import { redirect } from "next/navigation";

// Oude AV1-route (vóór 0.2.3): logboeken openen nu als detailpaneel in de wachtrij.
export default async function Page({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const job = (await searchParams).job;
  redirect(typeof job === "string" && /^\d+$/.test(job) ? `/av1/wachtrij?job=${job}` : "/av1/wachtrij");
}
