"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { useMemo } from "react";

export type Page<T> = { items: T[]; next_cursor: string | null };
export function usePages<T>(path: string) {
  return useInfiniteQuery({ queryKey: [path], initialPageParam: "", queryFn: ({ pageParam }) => api<Page<T>>(`${path}${path.includes("?") ? "&" : "?"}limit=25${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ""}`), getNextPageParam: page => page.next_cursor ?? undefined, refetchInterval: 15000 });
}
export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof ApiError && error.status === 404 ? "资源不存在，或不属于当前工作区。" : error instanceof ApiError && error.status === 403 ? "权限不足，无法执行此操作。" : error instanceof Error ? error.message : "服务请求失败，请重试。";
  return <p role="alert" className="break-words rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">{message}{error instanceof ApiError && <span className="ml-2 text-xs">({error.code})</span>}</p>;
}
export function More({ query }: { query: { hasNextPage: boolean; isFetchingNextPage: boolean; fetchNextPage: () => unknown; isError?: boolean; isFetchNextPageError?: boolean; isFetching?: boolean; refetch?: () => unknown } }) {
  return <div className="flex flex-wrap gap-2">{query.isError && <Button variant="outline" disabled={query.isFetching} onClick={() => void (query.isFetchNextPageError ? query.fetchNextPage() : query.refetch?.())}>重试加载</Button>}{query.hasNextPage && <Button variant="outline" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>{query.isFetchingNextPage ? "加载中…" : "加载更多"}</Button>}</div>;
}
export function JsonView({ value }: { value: unknown }) {
  return <pre className="max-w-full whitespace-pre-wrap break-all rounded-md bg-black/5 p-3 text-xs leading-6">{JSON.stringify(value, null, 2)}</pre>;
}
export function Time({ value }: { value?: string | null }) {
  return <span>{value ? new Date(value).toLocaleString() : "—"}</span>;
}
export function EmailPreview({ html, text }: { html?: string | null; text?: string | null }) {
  const document = useMemo(() => {
    if (!html || typeof window === "undefined") return "";
    const parsed = new DOMParser().parseFromString(html, "text/html");
    const allowed: Record<string, true> = { P: true, DIV: true, SPAN: true, H1: true, H2: true, H3: true, H4: true, TABLE: true, TBODY: true, THEAD: true, TR: true, TD: true, TH: true, UL: true, OL: true, LI: true, STRONG: true, B: true, EM: true, I: true, BR: true, HR: true, A: true, BLOCKQUOTE: true };
    for (const element of Array.from(parsed.body.querySelectorAll("*"))) {
      if (!Object.hasOwn(allowed, element.tagName)) { element.remove(); continue; }
      for (const attribute of Array.from(element.attributes)) {
        if (attribute.name !== "href" || element.tagName !== "A" || !/^https?:\/\//i.test(attribute.value)) element.removeAttribute(attribute.name);
      }
    }
    return `<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; form-action 'none'; base-uri 'none'"><style>body{font:14px/1.6 system-ui;padding:16px;overflow-wrap:anywhere}table{max-width:100%}a{color:#286b61}</style></head><body>${parsed.body.innerHTML}</body></html>`;
  }, [html]);
  return <div className="min-w-0 space-y-3">{document && <iframe title="隔离邮件预览" sandbox="" referrerPolicy="no-referrer" srcDoc={document} className="h-80 w-full rounded-md border bg-white" />}<pre className="max-w-full whitespace-pre-wrap break-all text-sm">{text || (!html ? "尚无已渲染邮件正文。" : "")}</pre></div>;
}
