import type { Metadata } from "next";
import { Providers } from "@/lib/providers";
import "./globals.css";

export const metadata: Metadata = { title: { default: "观测 · WebMonitor", template: "%s · 观测" }, description: "通过对话配置网页监控，真实采集、确认创建，查看变化证据与邮件通知。", referrer: "no-referrer" };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="zh-CN"><body><Providers>{children}</Providers></body></html>;
}
