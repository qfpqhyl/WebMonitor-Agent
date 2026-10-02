"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowRight, Radio } from "lucide-react";
import { api, ApiError, safeReturnTo } from "@/lib/api/client";
import { useAuth } from "@/lib/providers";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";


export function AuthForm({ register = false }: { register?: boolean }) {
  const router = useRouter();
  const { refresh } = useAuth();
  const [invitation, setInvitation] = useState("");
  const [returnTo, setReturnTo] = useState("/app");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const captured = useRef(false);
  useEffect(() => {
    if (captured.current) return;
    captured.current = true;
    const url = new URL(window.location.href);
    setReturnTo(safeReturnTo(url.searchParams.get("returnTo")));
    if (register) {
      setInvitation(url.searchParams.get("invitation_token") ?? url.searchParams.get("token") ?? "");
      url.searchParams.delete("invitation_token"); url.searchParams.delete("token");
      window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    }
  }, [register]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setPending(true);
    const data = new FormData(event.currentTarget);
    try {
      await api(register ? "/auth/register" : "/auth/login", { method: "POST", body: JSON.stringify({ email: data.get("email"), password: data.get("password"), ...(register ? { display_name: data.get("display_name"), invitation_token: invitation } : {}) }) });
      await refresh(); router.replace(returnTo);
    } catch (err) {
      setError(err instanceof ApiError ? ({ invalid_credentials: "邮箱或密码不正确。", registration_failed: "邀请无效、已过期或与邮箱不匹配，请联系管理员。", rate_limited: "尝试次数过多，请稍后重试。", forbidden: "请求未获授权，请刷新页面后再试。" }[err.code] ?? err.message) : "无法连接服务，请稍后重试。");
    } finally { setPending(false); }
  }
  return <main className="auth-layout"><Link className="brand" href="/"><Radio size={23} />观测 · WebMonitor</Link><section className="auth-card"><p className="eyebrow">{register ? "受邀加入工作区" : "欢迎回来"}</p><h1>{register ? "让重要变化，不再错过。" : "继续你的观测。"}</h1><p className="muted">{register ? "使用邀请对应的邮箱创建账户。邀请只在本页内存中保留，刷新后请重新打开邀请链接。" : "登录后管理监控、查看证据与通知记录。"}</p><form onSubmit={submit} className="form-stack" aria-describedby={error ? "auth-error" : undefined}>
    {register && <div><Label htmlFor="display_name">你的名字</Label><Input id="display_name" name="display_name" autoComplete="name" required maxLength={100} /></div>}
    <div><Label htmlFor="email">邮箱</Label><Input id="email" name="email" type="email" autoComplete="email" required /></div>
    <div><Label htmlFor="password">密码</Label><Input id="password" name="password" type="password" autoComplete={register ? "new-password" : "current-password"} minLength={register ? 12 : undefined} maxLength={128} required />{register && <p className="field-help">12–128 个字符。请勿使用其他账户的密码。</p>}</div>
    {register && !invitation && <p className="notice">未找到邀请凭证。请通过管理员提供的完整邀请链接进入。</p>}
    {error && <p id="auth-error" className="error" role="alert">{error}</p>}
    <Button type="submit" disabled={pending || (register && !invitation)}>{pending ? "正在提交…" : register ? "创建账户" : "登录"}<ArrowRight size={16} /></Button>
  </form><p className="auth-footer">{register ? <Link href="/login">已有账户？前往登录</Link> : "首次使用？请向管理员申请邀请。"}</p></section><p className="auth-footnote">真实采集 · 明确确认 · 可追溯证据</p></main>;
}
