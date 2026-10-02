import { MonitorDetail } from "@/features/monitors/monitor-detail";

export default async function MonitorPage({ params, searchParams }: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ run?: string | string[] }>;
}) {
  const { id } = await params;
  const { run } = await searchParams;
  return <MonitorDetail id={id} initialRun={typeof run === "string" ? run : undefined} />;
}
