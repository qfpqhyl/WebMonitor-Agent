"use client";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@/lib/api/schema";
import { api } from "@/lib/api/client";
import { useAuth } from "@/lib/providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ErrorNotice, JsonView, More, Time, usePages } from "@/features/notifications/resource-ui";
type Event = components["schemas"]["EventView"];
function EventCard({ event }: { event: Event }) {
  const { user } = useAuth(); const cache = useQueryClient();
  const monitor = useQuery({ queryKey: [`/monitors/${event.monitor_id}`], queryFn: () => api<components["schemas"]["MonitorDetail"]>(`/monitors/${event.monitor_id}`) });
  const allowed = user?.role === "admin" || (!!user && monitor.data?.created_by_user_id === user.user_id);
  const ack = useMutation({ mutationFn: () => api<Event>(`/events/${event.id}/acknowledge`, { method: "POST" }), onSuccess: () => cache.invalidateQueries({ queryKey: ["/events"] }) });
  return <Card><CardContent className="min-w-0 space-y-3 pt-5"><div className="flex flex-wrap justify-between gap-2"><h2 className="font-semibold">{event.type}</h2><span className="text-sm"><Time value={event.detected_at} /></span></div><p className="break-words text-sm">{monitor.data?.name ?? event.monitor_id}</p><JsonView value={event.change} /><div className="flex flex-wrap gap-3 text-sm"><Link className="underline" href={`/app/monitors/${event.monitor_id}`}>监控详情</Link><Link className="underline" href={`/app/monitors/${event.monitor_id}?run=${event.run_id}`}>运行及证据</Link></div><ErrorNotice error={ack.error || monitor.error} />{event.acknowledged_at ? <p className="text-sm">已确认 · <Time value={event.acknowledged_at} /></p> : allowed ? <Button variant="outline" disabled={ack.isPending} onClick={() => ack.mutate()}>{ack.isPending ? "确认中…" : "标记已知悉"}</Button> : <p className="text-sm">仅任务创建者或管理员可标记已知悉。</p>}</CardContent></Card>;
}
export function EventsPage() {
  const events = usePages<Event>("/events");
  return <div className="min-w-0 space-y-4"><h1 className="text-2xl font-semibold">变化与故障事件</h1><p className="text-sm">仅显示真实采集产生的事件。确认事件不等于邮件已送达。</p><ErrorNotice error={events.error} />{events.isPending && <p role="status">加载事件…</p>}{events.data?.pages[0].items.length === 0 && <p>暂无事件。首次成功采集建立基线，不发送业务变化通知。</p>}{events.data?.pages.flatMap(p => p.items).map(e => <EventCard event={e} key={e.id} />)}<More query={events} /></div>;
}
