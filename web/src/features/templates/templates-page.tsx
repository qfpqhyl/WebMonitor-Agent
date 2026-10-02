"use client";
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import type { components } from "@/lib/api/schema";
import { api } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { EmailPreview, ErrorNotice, More, Time, usePages } from "@/features/notifications/resource-ui";
type Template = components["schemas"]["Template"];
type Event = components["schemas"]["EventView"];
function Preview({ template, events }: { template: Template; events: Event[] }) {
  const [id, setId] = useState("");
  const preview = useMutation({ mutationFn: () => { const event = events.find(e => e.id === id); if (!event) throw new Error("请选择真实事件。"); return api<components["schemas"]["RenderedEmail"]>(`/email-templates/${template.id}/preview`, { method: "POST", body: JSON.stringify(event.payload) }); } });
  const matching = events.filter(e => e.type === template.event_type);
  return <div className="space-y-3">{matching.length ? <><Label htmlFor={`event-${template.id}`}>使用真实事件预览</Label><select id={`event-${template.id}`} className="w-full min-w-0 rounded-md border p-2 text-sm" value={id} onChange={e => { setId(e.target.value); preview.reset(); }}><option value="">选择匹配事件</option>{matching.map(e => <option key={e.id} value={e.id}>{new Date(e.detected_at).toLocaleString()} · {e.id}</option>)}</select><Button variant="outline" disabled={!id || preview.isPending} onClick={() => preview.mutate()}>{preview.isPending ? "渲染中…" : "预览邮件"}</Button></> : <p className="text-sm">暂无匹配的真实事件。请先完成监控采集并产生 {template.event_type} 事件，再预览；不会生成演示业务数据。</p>}<ErrorNotice error={preview.error} />{preview.data && <><p className="font-medium">{preview.data.subject}</p><EmailPreview html={preview.data.preview_html} text={preview.data.text} /></>}</div>;
}
export function TemplatesPage() {
  const templates = usePages<Template>("/email-templates"); const events = usePages<Event>("/events");
  return <div className="min-w-0 space-y-5"><h1 className="text-2xl font-semibold">默认邮件模板</h1><p className="text-sm">服务器内置只读版本。预览使用本工作区真实事件，不发送邮件。</p><ErrorNotice error={templates.error || events.error} />{templates.isPending && <p role="status">加载模板…</p>}{events.isPending && <p role="status">加载真实事件预览数据…</p>}{templates.data?.pages[0].items.length === 0 && <p>未找到默认模板，请检查数据库初始化。</p>}{templates.data?.pages.flatMap(p => p.items).map(t => <Card className="min-w-0" key={t.id}><CardHeader><CardTitle className="break-words">{t.name} · v{t.version}</CardTitle><p className="text-sm">{t.event_type} · <Time value={t.created_at} /></p></CardHeader><CardContent>{events.data && <Preview template={t} events={events.data.pages.flatMap(p => p.items)} />}</CardContent></Card>)}<More query={templates} /><div className="space-y-2">{events.hasNextPage && <p className="text-sm">加载更多历史事件以选择预览数据。</p>}<More query={events} /></div></div>;
}
