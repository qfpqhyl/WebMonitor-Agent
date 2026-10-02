"use client";
import { useState } from "react";
import Link from "next/link";
import type { components } from "@/lib/api/schema";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ConversationPanel } from "@/features/conversation/conversation-panel";
import { ErrorNotice, More, Time, usePages } from "@/features/notifications/resource-ui";
export function MonitorsPage() {
  const monitors = usePages<components["schemas"]["MonitorView"]>("/monitors"); const [create, setCreate] = useState(false);
  return <div className="min-w-0 space-y-5"><header className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-semibold">监控任务</h1><Button onClick={() => setCreate(true)}>通过对话创建</Button></header><ErrorNotice error={monitors.error} />{monitors.isPending && <p role="status">加载任务…</p>}{monitors.data?.pages[0].items.length === 0 && <Card><CardContent className="space-y-3 pt-6"><h2 className="font-medium">还没有监控任务</h2><p className="text-sm">描述目标页面、关注字段和通知条件。审核真实提取与邮件预览后，再确认创建。</p><Button onClick={() => setCreate(true)}>开始创建</Button></CardContent></Card>}<div className="grid min-w-0 gap-4 md:grid-cols-2">{monitors.data?.pages.flatMap(p => p.items).map(m => <Card className="min-w-0" key={m.id}><CardHeader><CardTitle><Link className="break-words underline decoration-transparent hover:decoration-current" href={`/app/monitors/${m.id}`}>{m.name}</Link></CardTitle></CardHeader><CardContent className="space-y-2 text-sm"><p className="break-all">{m.url}</p><p>{m.status} · {m.collection_mode} · 健康：{m.health_status}</p><p>基线：{m.baseline_status === "ready" ? "已建立" : "等待首次成功采集"}</p><p>下次计划：<Time value={m.next_run_at} /></p><Link className="inline-block underline" href={`/app/monitors/${m.id}`}>查看运行与证据</Link></CardContent></Card>)}</div><More query={monitors} /><Dialog open={create} onOpenChange={setCreate}><DialogContent className="max-h-[90dvh] max-w-3xl overflow-y-auto"><DialogHeader><DialogTitle>对话创建监控</DialogTitle></DialogHeader><ConversationPanel /></DialogContent></Dialog></div>;
}
