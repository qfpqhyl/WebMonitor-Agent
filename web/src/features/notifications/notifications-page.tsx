"use client";
import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@/lib/api/schema";
import { api } from "@/lib/api/client";
import { useAuth } from "@/lib/providers";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { EmailPreview, ErrorNotice, JsonView, More, Time, usePages } from "./resource-ui";
type Group = components["schemas"]["Group"];
type Delivery = components["schemas"]["Delivery"];
type GroupSpec = components["schemas"]["NotificationGroupSpec"];

function GroupEditor({ group, close }: { group?: Group; close: () => void }) {
  const cache = useQueryClient();
  const [name, setName] = useState(group?.name ?? "");
  const [enabled, setEnabled] = useState(group?.enabled ?? true);
  const [members, setMembers] = useState<GroupSpec["members"]>(group?.members.map(({ email, active }) => ({ email, active })) ?? []);
  const save = useMutation({ mutationFn: () => api<Group>(group ? `/notification-groups/${group.id}` : "/notification-groups", { method: group ? "PUT" : "POST", body: JSON.stringify({ name, enabled, members } satisfies GroupSpec) }), onSuccess: async () => { await cache.invalidateQueries({ queryKey: ["/notification-groups"] }); close(); } });
  return <form className="space-y-4" aria-describedby={save.error ? "group-save-error" : undefined} onSubmit={e => { e.preventDefault(); save.mutate(); }}><fieldset disabled={save.isPending} className="min-w-0 space-y-4">
    <Label htmlFor="group-name">组名称</Label><Input id="group-name" required maxLength={200} aria-invalid={!!save.error} value={name} onChange={e => setName(e.target.value)} />
    <label className="flex items-center gap-2"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />启用通知组</label>
    <p className="text-sm">收件人必须显式填写；不会自动添加工作区成员。</p>
    {members.map((member, index) => <div key={index} className="flex min-w-0 flex-wrap items-center gap-2"><Label htmlFor={`member-${index}`} className="sr-only">收件人 {index + 1} 邮箱</Label><Input id={`member-${index}`} type="email" required className="min-w-0 flex-1" value={member.email} onChange={e => setMembers(members.map((m, i) => i === index ? { ...m, email: e.target.value } : m))} /><label className="flex gap-1 text-sm"><input type="checkbox" checked={member.active} onChange={e => setMembers(members.map((m, i) => i === index ? { ...m, active: e.target.checked } : m))} />有效</label><Button type="button" variant="ghost" onClick={() => setMembers(members.filter((_, i) => i !== index))}>移除</Button></div>)}
    <Button type="button" variant="outline" onClick={() => setMembers([...members, { email: "", active: true }])}>添加收件人</Button><div id="group-save-error"><ErrorNotice error={save.error} /></div><div><Button disabled={save.isPending || !members.length}>{save.isPending ? "保存中…" : "保存通知组"}</Button></div>
  </fieldset></form>;
}
export function NotificationsPage() {
  const { user } = useAuth(); const admin = user?.role === "admin";
  const groups = usePages<Group>("/notification-groups"); const deliveries = usePages<Delivery>("/email-deliveries");
  const cache = useQueryClient(); const [editing, setEditing] = useState<Group | "new" | null>(null); const [selected, setSelected] = useState<string | null>(null);
  const detail = useQuery({ queryKey: ["/email-deliveries", selected], enabled: !!selected, queryFn: () => api<Delivery>(`/email-deliveries/${selected}`), refetchInterval: query => query.state.data && ["queued", "sending", "retrying"].includes(query.state.data.status) ? 3000 : false });
  const remove = useMutation({ mutationFn: (id: string) => api<Group>(`/notification-groups/${id}`, { method: "DELETE" }), onSuccess: () => cache.invalidateQueries({ queryKey: ["/notification-groups"] }) });
  return <div className="min-w-0 space-y-6"><header className="flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-semibold">通知</h1>{admin && <Button onClick={() => setEditing("new")}>新建通知组</Button>}</header>
    <section className="space-y-3"><h2 className="text-lg font-semibold">通知组</h2>{!admin && <p className="text-sm">通知组仅管理员可编辑，成员可在监控中选择现有组。</p>}<ErrorNotice error={groups.error || remove.error} />{groups.isPending && <p role="status">加载通知组…</p>}{groups.data?.pages[0].items.length === 0 && <p>暂无通知组。请管理员创建并填写收件人。</p>}
    {groups.data?.pages.flatMap(p => p.items).map(group => <Card key={group.id}><CardHeader><CardTitle>{group.name} · {group.enabled ? "启用" : "禁用"}</CardTitle></CardHeader><CardContent className="space-y-3"><ul className="break-words text-sm">{group.members.map(m => <li key={m.id}>{m.email} · {m.active ? "有效" : "停用"}</li>)}</ul>{admin && <div className="flex flex-wrap gap-2"><Button variant="outline" onClick={() => setEditing(group)}>编辑</Button><Button variant="destructive" disabled={remove.isPending} onClick={() => { if (window.confirm(`禁用并移除通知组“${group.name}”？历史记录将保留。`)) remove.mutate(group.id); }}>移除通知组</Button></div>}</CardContent></Card>)}<More query={groups} /></section>
    <section className="space-y-3"><h2 className="text-lg font-semibold">邮件投递</h2><p className="text-sm">sent 表示 SMTP 服务器已接收，不代表收件箱已收到。网络重试可能导致重复邮件。</p><ErrorNotice error={deliveries.error} />{deliveries.isPending && <p role="status">加载投递记录…</p>}{deliveries.data?.pages[0].items.length === 0 && <p>暂无投递记录。只有真实事件才会生成邮件。</p>}{deliveries.data?.pages.flatMap(p => p.items).map(d => <Card key={d.id}><CardContent className="space-y-2 pt-5"><p className="break-words font-medium">{d.recipient} · {d.status}</p><p className="text-sm"><Time value={d.created_at} /> · 尝试 {d.attempt_count} 次</p>{d.error_code && <p className="break-words text-red-700">{d.error_code}: {d.error_message}</p>}<div className="flex gap-3"><Button variant="outline" onClick={() => setSelected(d.id)}>查看投递详情</Button><Link className="self-center text-sm underline" href="/app/events">查看事件</Link></div></CardContent></Card>)}<More query={deliveries} /></section>
    <Dialog open={editing !== null} onOpenChange={open => { if (!open) setEditing(null); }}><DialogContent className="max-h-[85dvh] overflow-y-auto"><DialogHeader><DialogTitle>{editing === "new" ? "新建通知组" : "编辑通知组"}</DialogTitle></DialogHeader>{editing && <GroupEditor key={editing === "new" ? "new" : editing.id} group={editing === "new" ? undefined : editing} close={() => setEditing(null)} />}</DialogContent></Dialog>
    <Dialog open={!!selected} onOpenChange={open => { if (!open) setSelected(null); }}><DialogContent className="max-h-[85dvh] overflow-y-auto"><DialogHeader><DialogTitle>投递详情</DialogTitle></DialogHeader><ErrorNotice error={detail.error} />{detail.isPending && <p role="status">加载详情…</p>}{detail.isError && <Button variant="outline" onClick={() => void detail.refetch()}>重试加载详情</Button>}{detail.data && <div className="min-w-0 space-y-3"><p className="break-words">{detail.data.recipient} · {detail.data.status} · {detail.data.rendered_subject}</p><p className="break-all text-xs">Message-ID: {detail.data.message_id}</p><p>模板版本：{detail.data.template_version}</p><p>SMTP 响应：{detail.data.smtp_response_code ?? "—"} · 尝试 {detail.data.attempt_count} 次</p><dl className="text-sm"><div><dt className="inline">最后尝试：</dt><dd className="inline"><Time value={detail.data.last_attempt_at} /></dd></div><div><dt className="inline">服务器接收：</dt><dd className="inline"><Time value={detail.data.sent_at} /></dd></div><div><dt className="inline">失败：</dt><dd className="inline"><Time value={detail.data.failed_at} /></dd></div><div><dt className="inline">取消：</dt><dd className="inline"><Time value={detail.data.cancelled_at} /></dd></div></dl>{detail.data.error_code && <p role="alert" className="break-words text-sm text-red-800">{detail.data.error_code}: {detail.data.error_message}</p>}<EmailPreview html={detail.data.rendered_html} text={detail.data.rendered_text} /><h3>冻结的收件人来源</h3><JsonView value={detail.data.recipient_sources} /><h3>真实事件载荷</h3><JsonView value={detail.data.payload} /></div>}</DialogContent></Dialog>
  </div>;
}
