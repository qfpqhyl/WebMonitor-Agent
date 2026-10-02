"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { Activity, Bell, FileText, LayoutDashboard, LogOut, Menu, Radio, X } from "lucide-react";
import { useAuth } from "@/lib/providers";
import { Button } from "@/components/ui/button";

const navigation = [{ href: "/app", label: "观测工作台", icon: LayoutDashboard }, { href: "/app/monitors", label: "监控任务", icon: Radio }, { href: "/app/events", label: "变化事件", icon: Activity }, { href: "/app/notifications", label: "通知与投递", icon: Bell }, { href: "/app/templates", label: "默认邮件模板", icon: FileText }];
export function AppShell({ children }: { children: ReactNode }) {
  const { user, isPending, error, logout, refresh } = useAuth();
  const pathname = usePathname(); const router = useRouter();
  const [open, setOpen] = useState(false); const [logoutError, setLogoutError] = useState("");
  useEffect(() => { if (!isPending && !user && !error) router.replace(`/login?returnTo=${encodeURIComponent(pathname)}`); }, [isPending, user, error, router, pathname]);
  useEffect(() => { setOpen(false); }, [pathname]);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", dismiss);
    return () => window.removeEventListener("keydown", dismiss);
  }, [open]);
  if (isPending) return <main className="center-state" role="status"><Radio className="spin" />正在恢复会话…</main>;
  if (!user) return <main className="center-state">{error ? <><h1>暂时无法加载工作区</h1><p className="error" role="alert">{error.message}</p><Button onClick={() => void refresh()}>重新连接</Button><Link href="/login">前往登录</Link></> : <p role="status">正在前往登录…</p>}</main>;
  return <div className="app-layout"><header className="mobile-header"><Link href="/app" className="brand"><Radio size={21} />观测</Link><Button variant="ghost" aria-label={open ? "关闭导航" : "打开导航"} aria-expanded={open} aria-controls="workspace-nav" onClick={() => setOpen(!open)}>{open ? <X /> : <Menu />}</Button></header>{open && <button className="sidebar-backdrop" onClick={() => setOpen(false)} aria-label="关闭导航遮罩" />}
    <aside className={`sidebar ${open ? "is-open" : ""}`}><Link href="/app" className="brand"><Radio size={24} />观测<span>WebMonitor</span></Link><p className="sidebar-caption">把变化，变成确定的消息。</p><nav id="workspace-nav" aria-label="工作区导航">{navigation.map(({ href, label, icon: Icon }) => { const current = href === "/app" ? pathname === href : pathname.startsWith(href); return <Link key={href} href={href} className={current ? "nav-item active" : "nav-item"} aria-current={current ? "page" : undefined}><Icon size={19} />{label}</Link>; })}</nav><div className="sidebar-bottom"><div className="workspace-user"><div className="avatar">{user.display_name.slice(0, 1)}</div><div><strong>{user.display_name}</strong><span>{user.role === "admin" ? "管理员" : "工作区成员"}</span></div></div>{logoutError && <p className="error" role="alert">{logoutError}</p>}<Button variant="ghost" onClick={async () => { try { await logout(); router.replace("/login"); } catch { setLogoutError("退出失败，请重试。"); } }}><LogOut size={16} />退出登录</Button><p className="field-help">本地开发工作区 · 非生产部署</p></div></aside><main className="workspace-main">{children}</main></div>;
}
