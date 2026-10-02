"use client";
import { useEffect, useState } from "react";
import { CheckCircle2, ExternalLink, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Draft } from "./state";

export function ApprovalCard({ draft, pending, canConfirm, canRecover, onConfirm, onModify, onRecover }: { draft: Draft; pending: boolean; canConfirm: boolean; canRecover: boolean; onConfirm: () => void; onModify: () => void; onRecover: () => void }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const preview = draft.preview;
  const spec = draft.spec;
  if (!preview || !spec) return <div className="notice">草稿尚未完成真实预览，请在对话中继续完善配置。</div>;
  const expired = Date.parse(preview.expires_at) <= now || !!preview.invalidated_at || preview.revision !== draft.revision || preview.spec_hash !== draft.spec_hash;
  const approved = draft.approval && draft.approval.revision === draft.revision && draft.approval.preview_id === preview.id && !draft.approval.invalidated_at && !draft.approval.consumed_at && Date.parse(draft.approval.expires_at) > now;
  const fields = Array.isArray(spec.fields) ? spec.fields as Record<string, unknown>[] : [];
  const target = typeof spec.url === "string" && /^https?:\/\//i.test(spec.url) ? spec.url : null;
  return <section className="approval-card" aria-label="创建前确认"><div className="section-heading"><div><p className="eyebrow"><ShieldCheck size={15} />由你决定，才会开始</p><h3>{String(spec.name ?? "监控草稿")}</h3></div><span className="badge">版本 {draft.revision}</span></div>
    <dl className="review-grid"><div><dt>目标页面</dt><dd>{target ? <a href={target} target="_blank" rel="noopener noreferrer">{target}<ExternalLink size={13} /></a> : "无有效地址"}</dd></div><div><dt>采集方式</dt><dd>{spec.collection_mode === "browser" ? "浏览器 · 动态页面" : "HTTP · 页面响应"}</dd></div><div><dt>覆盖范围</dt><dd>{String(preview.coverage.scope ?? "未知")} · {String(preview.coverage.description ?? "")}</dd></div><div><dt>比较基线</dt><dd>上次成功采集；首次成功仅建立基线，不发送变化通知。</dd></div><div><dt>预览有效期</dt><dd>{new Date(preview.expires_at).toLocaleString("zh-CN")}{expired && " · 已失效，请重新预览"}</dd></div><div><dt>通知收件人（去重后）</dt><dd>{preview.recipient_snapshot.map((r, i) => <span className="recipient" key={i}>{String(r.email ?? "")}</span>)}</dd></div></dl>
    <h4>字段与实际提取结果</h4><div className="field-reviews">{fields.map((field, i) => <div key={i}><strong>{String(field.name)} <span className="muted">{String(field.type)}</span></strong><p>{String(field.semantic ?? "")}</p><pre>{JSON.stringify(preview.extracted_data[String(field.name)], null, 2) ?? "未提取"}</pre><details><summary>查看选择器与提取契约</summary><pre>{JSON.stringify(field, null, 2)}</pre></details></div>)}</div>
    {([ ["调度（UTC 执行，指定时区显示）", spec.schedule], ["变化规则（百分比参考上次成功采集）", spec.change_rules], ["列表业务键", spec.business_key], ["浏览器步骤", spec.browser_steps], ["通知组", spec.notification_group_ids], ["默认模板版本绑定", preview.template_snapshot], ["规则模拟", preview.rule_simulation] ] as [string, unknown][]).map(([label, value]) => <details className="review-details" key={label}><summary>{label}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>)}
    <h4>采集证据</h4><div className="evidence-links">{preview.evidence_refs.map((ref, i) => <a key={i} href={`/api/v1/previews/${preview.id}/evidence/${encodeURIComponent(String(ref.id))}`} target="_blank" rel="noopener noreferrer">{ref.kind === "screenshot" ? "查看截图" : "下载页面文本证据"}<ExternalLink size={14} /></a>)}</div>
    <h4>默认邮件预览</h4>{preview.rendered_emails.map((mail, i) => <details className="mail-preview" key={i} open={i === 0}><summary>{String(mail.subject ?? mail.event_type ?? "邮件")}</summary>{typeof mail.preview_html === "string" ? <iframe title={`邮件预览 ${i + 1}`} sandbox="" referrerPolicy="no-referrer" srcDoc={mail.preview_html} /> : <pre>{String(mail.text ?? "预览内容不可用")}</pre>}</details>)}
    {preview.warnings.map((warning, i) => <p className="notice" key={i}>{warning}</p>)}
    {draft.task_id ? <p className="success"><CheckCircle2 size={17} />已创建任务 <a href={`/app/monitors/${draft.task_id}`}>查看监控详情</a></p> : <><p className="field-help">本次确认仅授权当前版本和预览，不授予未来修改的永久权限。{!canConfirm && !canRecover && " 当前没有可用的审批请求，请继续修改并要求重新预览。"}</p><div className="button-row"><Button disabled={pending || expired || (approved ? !canRecover : !canConfirm)} onClick={approved ? onRecover : onConfirm}>{pending ? "正在处理…" : approved ? "恢复已批准的创建" : "确认创建"}</Button><Button variant="outline" disabled={pending} onClick={onModify}>继续修改</Button></div></>}
  </section>;
}
