"use client";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@/lib/api/schema";
import { api } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { EmailPreview, ErrorNotice, JsonView, More, Time, usePages } from "@/features/notifications/resource-ui";
type Schemas = components["schemas"];
type RouteView = Schemas["RouteView"];
type Job = Schemas["RoutePreviewJob"];
type Preview = Schemas["RoutePreview"];
const eventTypes = ["price_changed", "list_changed", "content_changed", "run_failed", "recovered"] as const;
export function NotificationRoutes({ monitorId, editable }: { monitorId: string; editable: boolean }) {
  const path = `/monitors/${monitorId}/notification-routes`; const cache = useQueryClient();
  const current = useQuery({ queryKey: [path], queryFn: () => api<RouteView>(path) });
  const groups = usePages<Schemas["Group"]>("/notification-groups"); const templates = usePages<Schemas["Template"]>("/email-templates");
  const [selection, setSelection] = useState<RouteView | null>(null); const [job, setJob] = useState<Job | null>(null); const [preview, setPreview] = useState<Preview | null>(null); const [key, setKey] = useState("");
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { if (!preview) return; const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, [preview]);
  useEffect(() => { if (current.data) setSelection(current.data); }, [current.data]);
  const request = useMutation({ mutationFn: () => api<Job>(`${path}/preview`, { method: "POST", body: JSON.stringify({ base_version_id: selection!.base_version_id, notification_group_ids: selection!.notification_group_ids, template_bindings: selection!.template_bindings }) }), onSuccess: result => { setJob(result); setPreview(null); setKey(crypto.randomUUID()); } });
  const status = useQuery({ queryKey: [path, "preview", job?.collection_job_id], enabled: !!job && !preview, queryFn: () => api<Schemas["PreviewJobStatus"]>(`${path}/preview/${job!.collection_job_id}`), refetchInterval: query => ["queued", "running"].includes(query.state.data?.status ?? "queued") ? 1500 : false });
  const materialize = useMutation({ mutationFn: () => api<Preview>(`${path}/preview/${job!.collection_job_id}`, { method: "POST", body: JSON.stringify({ draft_id: job!.draft_id, revision: job!.revision }) }), onSuccess: setPreview });
  const confirm = useMutation({ mutationFn: () => api<Schemas["RouteUpdate"]>(`${path}/confirm`, { method: "POST", body: JSON.stringify({ draft_id: job!.draft_id, revision: job!.revision, preview_id: preview!.id, base_version_id: job!.base_version_id, idempotency_key: key }) }), onSuccess: async () => { setPreview(null); setJob(null); await Promise.all([cache.invalidateQueries({ queryKey: [path] }), cache.invalidateQueries({ queryKey: [`/monitors/${monitorId}`] }), cache.invalidateQueries({ queryKey: ["/monitors"] }), cache.invalidateQueries({ queryKey: ["monitors", "summary"] })]); } });
  const locked = !!job || request.isPending;
  const change = (value: RouteView) => { setSelection(value); setPreview(null); confirm.reset(); };
  return <section className="min-w-0 space-y-4">
    <h2 className="text-lg font-semibold">通知路由</h2>
    <ErrorNotice error={current.error || groups.error || templates.error || request.error || status.error || materialize.error || confirm.error} />
    {current.isPending && <p role="status">加载路由…</p>}
    {current.isError && <Button variant="outline" onClick={() => void current.refetch()}>重试加载路由</Button>}
    {selection && <>
      <p className="text-sm">当前版本 {selection.version}。修改必须完成真实采集预览并显式确认后发布新版本。</p>
      {!editable && <p className="text-sm">当前为只读；仅任务创建者或管理员可修改通知路由。</p>}
      <fieldset disabled={!editable || locked} className="min-w-0 space-y-3">
        <legend className="font-medium">收件人通知组</legend>
        {groups.isPending && <p role="status">加载通知组…</p>}
        {groups.data?.pages[0].items.length === 0 && <p className="text-sm">暂无通知组，请管理员创建。</p>}
        {groups.data?.pages.flatMap(p => p.items).map(g => <label className="flex min-w-0 gap-2 text-sm" key={g.id}><input type="checkbox" checked={selection.notification_group_ids.includes(g.id)} disabled={!g.enabled && !selection.notification_group_ids.includes(g.id)} onChange={e => change({ ...selection, notification_group_ids: e.target.checked ? [...selection.notification_group_ids, g.id] : selection.notification_group_ids.filter(id => id !== g.id) })} /><span className="min-w-0 break-words">{g.name} {g.enabled ? "" : "（禁用）"} · {g.members.filter(m => m.active).map(m => m.email).join(", ") || "无有效收件人"}</span></label>)}
        {templates.isPending && <p role="status">加载模板…</p>}
        {eventTypes.map(type => <div key={type} className="space-y-1"><Label htmlFor={`route-${type}`}>{type}</Label><select id={`route-${type}`} className="w-full min-w-0 rounded-md border bg-white p-2 text-sm" value={selection.template_bindings[type]?.template_id ?? ""} onChange={e => { const template = templates.data?.pages.flatMap(p => p.items).find(t => t.id === e.target.value); if (template) change({ ...selection, template_bindings: { ...selection.template_bindings, [type]: { template_id: template.id, version: template.version } } }); }}><option value="" disabled>选择默认模板</option>{templates.data?.pages.flatMap(p => p.items).filter(t => t.event_type === type).map(t => <option key={t.id} value={t.id}>{t.name} · v{t.version}</option>)}</select></div>)}
      </fieldset>
      <More query={groups} /><More query={templates} />
      {editable && !job && <Button disabled={request.isPending || !selection.notification_group_ids.length || groups.isPending || templates.isPending} onClick={() => { materialize.reset(); confirm.reset(); request.mutate(); }}>{request.isPending ? "提交预览…" : "真实采集并预览修改"}</Button>}
      {job && <div className="space-y-3"><p role="status">采集状态：{status.data?.status ?? job.status}{status.data?.error_code && ` · ${status.data.error_code}`}</p>{status.isError && <Button variant="outline" onClick={() => void status.refetch()}>重试查询采集状态</Button>}{status.data?.status === "succeeded" && !preview && <Button disabled={materialize.isPending} onClick={() => materialize.mutate()}>{materialize.isPending ? "生成中…" : "生成并查看预览"}</Button>}<Button variant="outline" disabled={confirm.isPending} onClick={() => { setJob(null); setPreview(null); materialize.reset(); request.reset(); confirm.reset(); }}>继续修改（旧预览不再用于确认）</Button></div>}
      {preview && <div className="min-w-0 space-y-3 rounded-md border p-4"><h3 className="font-medium">确认前审核</h3><p>有效期至 <Time value={preview.expires_at} /></p><h4>实际提取数据 / 覆盖范围</h4><JsonView value={{ fields: preview.extracted_data, coverage: preview.coverage }} /><h4>去重收件人</h4><JsonView value={preview.recipient_snapshot} /><h4>模板版本</h4><JsonView value={preview.template_snapshot} /><h4>规则模拟</h4><JsonView value={preview.rule_simulation} /><h4>采集证据</h4>{preview.evidence_refs.length === 0 && <p className="text-sm">没有可用证据。</p>}{preview.evidence_refs.map((e, i) => typeof e.id === "string" && <a key={i} target="_blank" rel="noopener noreferrer" className="block break-all text-sm underline" href={`/api/v1/previews/${preview.id}/evidence/${encodeURIComponent(e.id)}`}>{e.kind === "screenshot" ? "查看截图" : "下载文本证据"}</a>)}{preview.warnings.map((w, i) => <p key={i} className="break-words text-sm">{w}</p>)}{preview.rendered_emails.map((mail, i) => <div key={i} className="min-w-0"><p className="break-words font-medium">{String(mail.subject ?? "邮件预览")}</p><EmailPreview html={typeof mail.preview_html === "string" ? mail.preview_html : undefined} text={typeof mail.text === "string" ? mail.text : undefined} /></div>)}{Date.parse(preview.expires_at) <= now && <p role="alert">预览已过期，请继续修改并重新预览。</p>}<Button disabled={confirm.isPending || Date.parse(preview.expires_at) <= now} onClick={() => confirm.mutate()}>{confirm.isPending ? "发布中…" : "确认发布通知路由新版本"}</Button></div>}
      {confirm.isSuccess && <p role="status">已发布版本 {confirm.data.version}。</p>}
    </>}
  </section>;
}
