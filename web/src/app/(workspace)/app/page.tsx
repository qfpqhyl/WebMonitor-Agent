"use client";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Radio } from "lucide-react";
import { api } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { ConversationPanel } from "@/features/conversation/conversation-panel";

type Monitor = components["schemas"]["MonitorView"];
export default function WorkspacePage() {
  const monitors = useQuery({ queryKey: ["monitors", "summary"], queryFn: () => api<{ items: Monitor[]; next_cursor: string | null }>("/monitors?limit=5"), refetchInterval: 30000 });
  return <div className="dashboard"><header className="page-heading"><p className="eyebrow">WORKSPACE / 观测工作台</p><h1>把关注交给监控。<br /><span className="muted">把决定留给自己。</span></h1><p>先验证提取结果，再确认开始。每一次变化都有迹可循。</p></header><div className="dashboard-grid"><ConversationPanel /><aside className="dashboard-aside"><section className="summary-card"><div className="section-heading"><h2>最近的监控</h2><Link href="/app/monitors" aria-label="查看全部监控"><ArrowUpRight size={18} /></Link></div>{monitors.isPending ? <p role="status">正在加载任务…</p> : monitors.error ? <p className="error" role="alert">{monitors.error.message}</p> : monitors.data?.items.length ? <div className="summary-list">{monitors.data.items.map(m => <Link href={`/app/monitors/${m.id}`} key={m.id}><div><strong>{m.name}</strong><span className="muted">{m.collection_mode === "browser" ? "动态页面" : "HTTP 页面"} · {m.status === "active" ? "运行中" : m.status === "paused" ? "已暂停" : "已删除"}</span></div><span className={m.health_status === "failed" ? "badge error-badge" : "badge"}>{m.health_status === "failed" ? "采集故障" : m.baseline_status === "ready" ? "已建立基线" : "等待首次采集"}</span></Link>)}</div> : <div className="empty-summary"><Radio size={30} /><h3>还没有监控任务</h3><p>在左侧描述你的需求，完成预览与确认后，任务会显示在这里。</p></div>}</section><section className="guidance-card"><p className="eyebrow">如何开始</p><ol><li><strong>描述目标</strong><span>页面 URL、关注字段、变化条件和通知组。</span></li><li><strong>核对真实预览</strong><span>确认字段值、采集覆盖与邮件收件人。</span></li><li><strong>确认后持续观测</strong><span>首次成功建立基线，之后比较每次成功采集。</span></li></ol><Link href="/app/notifications">查看可用的通知组 <ArrowUpRight size={14} /></Link></section></aside></div></div>;
}
