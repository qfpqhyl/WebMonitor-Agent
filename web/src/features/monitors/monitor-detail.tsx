"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@/lib/api/schema";
import { api } from "@/lib/api/client";
import { useAuth } from "@/lib/providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ErrorNotice, JsonView, More, Time, usePages } from "@/features/notifications/resource-ui";
import { NotificationRoutes } from "./notification-routes";

type Schemas = components["schemas"];
type Run = Schemas["webmonitor__schemas__monitor_api__RunView"];

function RunDetails({ id, monitorId, editable, refresh }: { id: string; monitorId: string; editable: boolean; refresh: () => Promise<unknown> }) {
  const detail = useQuery({ queryKey: [`/runs/${id}`], queryFn: () => api<Schemas["RunDetail"]>(`/runs/${id}`), refetchInterval: query => ["queued", "running"].includes(query.state.data?.status ?? "queued") ? 2000 : false });
  const cancel = useMutation({ mutationFn: () => api<Run>(`/runs/${id}/cancel`, { method: "POST" }), onSuccess: async () => { await detail.refetch(); await refresh(); } });
  const run = detail.data;
  return <div className="min-w-0 space-y-4"><ErrorNotice error={detail.error || cancel.error} />{detail.isPending && <p role="status">加载运行详情…</p>}{detail.isError && <Button variant="outline" onClick={() => void detail.refetch()}>重试加载</Button>}{run && run.monitor_id !== monitorId && <p role="alert">此运行不属于当前监控。</p>}{run && run.monitor_id === monitorId && <>
    <p className="break-all text-xs">运行 {run.id}</p><p>{run.status} · {run.trigger} · 尝试 {run.attempt_count} 次</p>
    <dl className="space-y-1 text-sm"><div><dt className="inline">创建：</dt><dd className="inline"><Time value={run.created_at} /></dd></div><div><dt className="inline">开始：</dt><dd className="inline"><Time value={run.started_at} /></dd></div><div><dt className="inline">完成：</dt><dd className="inline"><Time value={run.finished_at} /></dd></div></dl>
    {run.error_code && <p role="alert" className="break-words text-red-800">{run.error_code}: {run.error_message}</p>}
    {run.warnings.map((warning, i) => <p className="break-words text-sm" key={i}>{warning}</p>)}
    {editable && ["queued", "running"].includes(run.status) && <Button variant="destructive" disabled={cancel.isPending} onClick={() => cancel.mutate()}>{cancel.isPending ? "取消中…" : "取消此运行"}</Button>}
    <section className="space-y-2"><h3 className="font-medium">前后差异</h3>{run.diff ? <JsonView value={run.diff} /> : <p className="text-sm">暂无可提交的差异。首次成功建立基线，失败不推进可信基线。</p>}</section>
    <section className="space-y-2"><h3 className="font-medium">实际提取结果</h3>{run.snapshot ? <><p className="break-all text-sm">最终页面：{run.snapshot.final_url}</p><p className="text-sm">采集时间：<Time value={run.snapshot.collected_at} /></p><JsonView value={run.snapshot.extracted_data} /><h4 className="font-medium">覆盖范围（bounded 不代表全量）</h4><JsonView value={run.snapshot.coverage} /></> : <p className="text-sm">此运行尚无成功快照。</p>}</section>
    <section className="space-y-2"><h3 className="font-medium">受权限保护的证据</h3>{!run.evidence.length && <p className="text-sm">本次运行没有可用证据。</p>}{run.evidence.map(e => <a key={e.id} className="block break-all text-sm underline" target="_blank" rel="noopener noreferrer" href={`/api/v1/runs/${run.id}/evidence/${e.id}`}>{e.kind === "screenshot" ? "查看截图" : "下载文本证据"} · {e.content_type} · {e.size_bytes} 字节</a>)}<p className="text-xs text-muted-foreground">页面 HTML 仅作为文本附件下载，不在站内执行。</p></section>
    <section className="space-y-2"><h3 className="font-medium">尝试记录</h3>{run.attempts.length === 0 && <p className="text-sm">等待 Worker 领取。</p>}{run.attempts.map(attempt => <div className="rounded-md border p-3 text-sm" key={attempt.id}><p>第 {attempt.attempt_number} 次 · {attempt.status} · {attempt.retryable ? "可重试" : "不可重试"}</p><p><Time value={attempt.started_at} /> → <Time value={attempt.finished_at} /></p>{attempt.error_code && <p className="break-words text-red-800">{attempt.error_code}: {attempt.error_message}</p>}</div>)}</section>
  </>}</div>;
}

export function MonitorDetail({ id, initialRun }: { id: string; initialRun?: string }) {
  const cache = useQueryClient(); const { user } = useAuth();
  const path = `/monitors/${id}`;
  const monitor = useQuery({ queryKey: [path], queryFn: () => api<Schemas["MonitorDetail"]>(path), refetchInterval: 10000 });
  const runs = usePages<Run>(`${path}/runs`);
  const [selected, setSelected] = useState<string | null>(initialRun ?? null);
  const editable = user?.role === "admin" || (!!user && monitor.data?.created_by_user_id === user.user_id);
  const refresh = async () => { await Promise.all([cache.invalidateQueries({ queryKey: [path] }), cache.invalidateQueries({ queryKey: [`${path}/runs`] }), cache.invalidateQueries({ queryKey: ["/monitors"] }), cache.invalidateQueries({ queryKey: ["monitors", "summary"] })]); };
  const toggle = useMutation({ mutationFn: (action: "pause" | "resume") => api<Schemas["MonitorView"]>(`${path}/${action}`, { method: "POST" }), onSuccess: refresh });
  const manual = useMutation({ mutationFn: () => api<Run>(`${path}/runs`, { method: "POST" }), onSuccess: async result => { setSelected(result.id); await refresh(); } });
  const m = monitor.data;
  return <div className="min-w-0 space-y-6"><Link className="text-sm underline" href="/app/monitors">← 所有监控</Link><ErrorNotice error={monitor.error || toggle.error || manual.error} />{monitor.isPending && <p role="status">加载监控…</p>}{monitor.isError && <Button variant="outline" onClick={() => void monitor.refetch()}>重试加载</Button>}{m && <>
    <header className="space-y-3"><h1 className="break-words text-2xl font-semibold">{m.name}</h1><p className="break-all text-sm">{m.url}</p><p>{m.status} · {m.collection_mode} · 健康：{m.health_status} · v{m.version}</p><p className="text-sm">下次计划：{m.status === "active" ? <Time value={m.next_run_at} /> : "已暂停"}</p>{editable ? <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={toggle.isPending} onClick={() => toggle.mutate(m.status === "active" ? "pause" : "resume")}>{toggle.isPending ? "更新中…" : m.status === "active" ? "暂停监控" : "恢复监控"}</Button>{m.status === "active" && <Button disabled={manual.isPending} onClick={() => manual.mutate()}>{manual.isPending ? "提交中…" : "立即运行"}</Button>}</div> : <p className="text-sm">只有任务创建者或管理员可以修改状态、启动或取消运行。</p>}</header>
    <section className="space-y-3"><h2 className="text-lg font-semibold">可信基线</h2><p className="text-sm">百分比变化参考上次成功采集，不是累计变化；失败不会覆盖基线。</p>{m.baseline ? <><p className="text-sm">上次成功：<Time value={m.baseline.collected_at} /></p><JsonView value={m.baseline.extracted_data} /><h3 className="font-medium">覆盖范围</h3><JsonView value={m.baseline.coverage} /></> : <p>等待首次成功采集建立基线。</p>}</section>
    <details className="min-w-0 rounded-md border p-4"><summary className="cursor-pointer font-medium">当前版本的字段、规则与调度合同</summary><div className="mt-3"><JsonView value={m.spec} /></div></details>
    <section className="space-y-3"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-lg font-semibold">运行记录</h2><Button variant="outline" disabled={runs.isFetching} onClick={() => void runs.refetch()}>刷新运行记录</Button></div><ErrorNotice error={runs.error} />{runs.isPending && <p role="status">加载运行…</p>}{runs.data?.pages[0].items.length === 0 && <p>尚未执行采集。</p>}{runs.data?.pages.flatMap(page => page.items).map(run => <Card className="min-w-0" key={run.id}><CardContent className="space-y-2 pt-5"><p>{run.status} · {run.trigger} · 尝试 {run.attempt_count} 次</p><p className="text-sm"><Time value={run.created_at} /></p>{run.error_code && <p className="break-words text-sm text-red-800">{run.error_code}: {run.error_message}</p>}<Button variant="outline" onClick={() => setSelected(run.id)}>查看提取、差异与证据</Button></CardContent></Card>)}<More query={runs} /></section>
    <NotificationRoutes monitorId={id} editable={editable} />
  </>}<Dialog open={!!selected} onOpenChange={open => { if (!open) setSelected(null); }}><DialogContent className="max-h-[90dvh] overflow-y-auto"><DialogHeader><DialogTitle>运行详情</DialogTitle></DialogHeader>{selected && <RunDetails key={selected} id={selected} monitorId={id} editable={editable} refresh={refresh} />}</DialogContent></Dialog></div>;
}
