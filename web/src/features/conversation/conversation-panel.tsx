"use client";

import Link from "next/link";
import { useEffect, useReducer, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowUp, Check, CircleAlert, Loader2, Plus, Square, Radio } from "lucide-react";
import { api, ApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { ApprovalCard } from "./approval-card";
import { conversationReducer, eventSchema, initialProjection, type Snapshot, type ConversationEvent, type Draft } from "./state";

function Markdown({ text }: { text: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: () => <span className="muted">[远程图片已禁用]</span>, a: ({ href, children }) => href && /^https?:\/\//i.test(href) ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span> }}>{text}</ReactMarkdown>;
}

export function ConversationPanel() {
  const client = useQueryClient();
  const [id, setId] = useState<string | null>(null);
  const [projection, dispatch] = useReducer(conversationReducer, initialProjection);
  const [content, setContent] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState("连接中");
  const textarea = useRef<HTMLTextAreaElement>(null);
  const source = useRef<EventSource | null>(null);
  const messageKey = useRef<{ content: string; key: string } | null>(null);
  const restoreCaptured = useRef(false);
  useEffect(() => {
    if (restoreCaptured.current) return;
    restoreCaptured.current = true;
    const url = new URL(window.location.href);
    const stored = url.searchParams.get("conversation") ?? localStorage.getItem("wm_conversation");
    if (stored && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(stored)) setId(stored);
  }, []);
  const snapshot = useQuery({ queryKey: ["conversation", id], queryFn: () => api<Snapshot>(`/agent/conversations/${id}`), enabled: !!id, refetchInterval: (query) => query.state.data?.runs.some(run => ["queued", "running", "waiting_approval"].includes(run.status)) ? 5000 : false });
  useEffect(() => { if (snapshot.data) dispatch({ type: "snapshot", snapshot: snapshot.data }); }, [snapshot.data]);
  const loaded = snapshot.data?.id === id;
  useEffect(() => {
    if (!id || !loaded) return;
    let closed = false;
    let cursor = 0;
    let baseline = snapshot.data?.last_seq ?? 0;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let buffer: ConversationEvent[] = [];
    let frame: ReturnType<typeof setTimeout> | undefined;
    function flush() { clearTimeout(frame); if (buffer.length) { dispatch({ type: "replay", conversationId: id!, events: buffer }); dispatch({ type: "events", events: buffer }); buffer = []; } frame = undefined; }
    function open() {
      if (closed) return;
      const stream = new EventSource(`/api/v1/agent/conversations/${id}/events?after=${cursor}`);
      source.current = stream;
      stream.onopen = () => setConnection("实时连接");
      stream.onmessage = event => {
        let parsed;
        try { parsed = eventSchema.safeParse(JSON.parse(event.data)); } catch { setError("事件格式异常，正在重新同步。"); stream.close(); void resync(); return; }
        if (!parsed.success || parsed.data.conversation_id !== id) { setError("事件校验失败，正在重新同步。"); stream.close(); void resync(); return; }
        const envelope = parsed.data;
        if (envelope.seq <= cursor) return;
        if (envelope.seq !== cursor + 1) { stream.close(); void resync(); return; }
        cursor = envelope.seq; buffer.push(envelope);
        if (!frame) frame = setTimeout(flush, 100);
        if (envelope.seq > baseline && envelope.type !== "message.delta") { flush(); void client.invalidateQueries({ queryKey: ["conversation", id] }); if (envelope.type === "run.completed") { void client.invalidateQueries({ queryKey: ["monitors"] }); void client.invalidateQueries({ queryKey: ["/monitors"] }); } }
      };
      stream.onerror = () => { stream.close(); setConnection("连接中断 · 正在同步"); void resync(); };
    }
    async function resync() {
      flush();
      try { const latest = await api<Snapshot>(`/agent/conversations/${id}`); if (closed) return; if (latest.last_seq < cursor) dispatch({ type: "reset" }); cursor = 0; baseline = latest.last_seq; client.setQueryData(["conversation", id], latest); dispatch({ type: "snapshot", snapshot: latest }); }
      catch (err) { if (!closed) setError(err instanceof ApiError ? err.message : "暂时无法同步对话，执行不会因断线而取消。"); }
      if (!closed) retry = setTimeout(open, 2000);
    }
    open();
    const close = () => { closed = true; source.current?.close(); clearTimeout(retry); clearTimeout(frame); };
    window.addEventListener("wm:session-ended", close);
    return () => { close(); window.removeEventListener("wm:session-ended", close); };
  // The subscription owns its cursor; snapshots must not restart a live connection.
  }, [id, loaded, client]);
  const active = snapshot.data?.runs.find(run => ["queued", "running", "waiting_approval"].includes(run.status));
  async function send(event: FormEvent) {
    event.preventDefault(); if (!content.trim() || pending || active) return;
    setPending(true); setError("");
    try {
      let conversationId = id;
      if (!conversationId) {
        const created = await api<Snapshot>("/agent/conversations", { method: "POST", body: JSON.stringify({}) });
        conversationId = created.id; client.setQueryData(["conversation", created.id], created); setId(created.id);
        localStorage.setItem("wm_conversation", created.id);
        const url = new URL(window.location.href); url.searchParams.set("conversation", created.id); window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
      }
      if (messageKey.current?.content !== content) messageKey.current = { content, key: crypto.randomUUID() };
      await api(`/agent/conversations/${conversationId}/messages`, { method: "POST", body: JSON.stringify({ content, client_message_id: messageKey.current.key }) });
      setContent(""); messageKey.current = null;
      await client.invalidateQueries({ queryKey: ["conversation", conversationId] });
    } catch (err) { setError(err instanceof ApiError ? err.message : "发送失败。你可以重试同一条消息，不会重复执行。"); } finally { setPending(false); }
  }
  async function action(kind: "confirm" | "cancel" | "create", draft?: Draft) {
    if (!id) return; setPending(true); setError("");
    try {
      const run = [...(snapshot.data?.runs ?? [])].reverse().find(item => item.pending_approval?.draft_id === draft?.id);
      const approval = run?.pending_approval;
      if (kind !== "cancel" && (!draft || !approval || !draft.preview)) throw new Error("服务端未提供有效的待确认授权，请在对话中重新预览。");
      if (kind === "cancel" && !active) { textarea.current?.focus(); return; }
      const body = kind === "cancel" ? { agent_run_id: active!.id } : kind === "confirm" ? { draft_id: draft!.id, revision: approval!.confirmed_revision, preview_id: draft!.preview!.id, idempotency_key: approval!.idempotency_key } : { draft_id: draft!.id, confirmed_revision: approval!.confirmed_revision, idempotency_key: approval!.idempotency_key };
      await api(`/agent/conversations/${id}/${kind}`, { method: "POST", body: JSON.stringify(body) });
      await client.invalidateQueries({ queryKey: ["conversation", id] });
      await client.invalidateQueries({ queryKey: ["monitors"] });
      await client.invalidateQueries({ queryKey: ["/monitors"] });
      if (kind === "cancel") textarea.current?.focus();
    } catch (err) { setError(err instanceof Error ? err.message : "操作失败，请重试。"); } finally { setPending(false); }
  }
  function newConversation() {
    source.current?.close(); setId(null); dispatch({ type: "reset" }); messageKey.current = null; setError(""); setContent(""); localStorage.removeItem("wm_conversation");
    const url = new URL(window.location.href); url.searchParams.delete("conversation"); window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  }
  return <section className="conversation-panel"><div className="section-heading"><div><p className="eyebrow"><Radio size={14} />对话创建</p><h2>告诉我，你想关注什么？</h2></div><Button variant="ghost" onClick={newConversation} disabled={pending}><Plus size={16} />新对话</Button></div><p className="muted">描述页面、变化条件和通知组。真实提取与邮件预览完成后，由你确认创建。</p>
    {id && <p className="connection-status" role="status"><span className={connection === "实时连接" ? "status-dot" : "status-dot offline"} />{connection} · 关闭页面不会停止服务端执行</p>}
    {snapshot.isPending && id && <p role="status">正在恢复对话…</p>}
    {snapshot.error && <p className="error" role="alert">{snapshot.error.message}<Button variant="ghost" onClick={() => void snapshot.refetch()}>重新加载</Button></p>}
    <div className="conversation-history" aria-live="polite" aria-relevant="additions text">{snapshot.data?.messages.map(message => <article className={`message message-${message.role}`} key={message.id}><span className="message-author">{message.role === "user" ? "你" : message.role === "assistant" ? "观测助手" : "系统"}</span><div className="markdown"><Markdown text={message.content} /></div></article>)}
    {Object.entries(projection.streams).filter(([runId]) => snapshot.data?.runs.some(run => run.id === runId && ["queued", "running"].includes(run.status))).map(([runId, text]) => <article className="message message-assistant" key={runId}><span className="message-author">观测助手 · 正在生成</span><div className="markdown"><Markdown text={text} /></div></article>)}
    {projection.conversationId === id && Object.entries(projection.tools).map(([key, tool]) => <details className="tool-card" key={key}><summary>{tool.status === "tool.started" ? <Loader2 size={15} className="spin" /> : tool.status === "tool.failed" ? <CircleAlert size={15} /> : <Check size={15} />}{tool.name}<span className="muted">{tool.status === "tool.started" ? "工具已开始" : tool.status === "tool.failed" ? "失败" : "已完成"}</span></summary><pre>{JSON.stringify(tool.detail, null, 2)}</pre></details>)}
    {snapshot.data?.runs.filter(run => run.error_code).map(run => <p className="error" key={run.id}>{run.error_code} · {run.error_message ?? "执行失败，草稿已保留。"}</p>)}
    {snapshot.data?.runs.filter(run => run.checkpoint_error).map(run => <p className="error" role="alert" key={`checkpoint-${run.id}`}>{run.checkpoint_error} · 审批状态无法恢复，请停止本轮并重新预览。</p>)}
    {snapshot.data?.drafts.map(draft => { const boundRun = [...snapshot.data!.runs].reverse().find(run => run.pending_approval?.draft_id === draft.id && run.pending_approval.confirmed_revision === draft.revision && run.pending_approval.preview_id === draft.preview?.id); return <ApprovalCard key={draft.id} draft={draft} pending={pending} canConfirm={boundRun?.status === "waiting_approval"} canRecover={!!boundRun && boundRun.status !== "cancelled"} onConfirm={() => void action("confirm", draft)} onModify={() => void action("cancel", draft)} onRecover={() => void action("create", draft)} />; })}
    {snapshot.data?.runs.filter(run => run.status === "completed" && run.result && typeof run.result.task_id === "string").map(run => <p className="success" key={run.id}>任务已创建 · <Link href={`/app/monitors/${String(run.result!.task_id)}`}>查看监控</Link></p>)}
    </div>
    {!id && <div className="conversation-intro"><p>从一句话开始，不必手动填写选择器。</p><p className="example">示例说明（不会自动执行）：监控某商品页面的当前价格，每分钟检查，较上次成功采集下降 10% 时通知指定组。</p></div>}
    {error && <p className="error" role="alert">{error}</p>}
    <form onSubmit={send} className="composer"><label htmlFor="monitor-message">监控需求或修改说明</label><textarea ref={textarea} id="monitor-message" value={content} onChange={event => setContent(event.target.value)} maxLength={16000} rows={3} placeholder="输入页面 URL、你关心的变化，以及通知组…" disabled={pending} aria-describedby="composer-help" /><div className="composer-bottom"><span id="composer-help">{active ? "本轮执行中。请先停止，再发送修改。" : "Enter 换行 · 点击发送"}</span><div className="button-row">{active && <Button type="button" variant="outline" disabled={pending} onClick={() => void action("cancel")}><Square size={14} />停止执行</Button>}<Button type="submit" disabled={pending || !!active || !content.trim()} aria-label="发送监控需求">{pending ? <Loader2 size={17} className="spin" /> : <ArrowUp size={18} />}发送</Button></div></div></form>
  </section>;
}
