import { z } from "zod";
import type { components } from "@/lib/api/schema";

export type Snapshot = components["schemas"]["ConversationSnapshot"];
export type Draft = components["schemas"]["DraftView"];
export const eventSchema = z.object({ schema_version: z.literal(1), event_id: z.string().uuid(), seq: z.number().int().positive(), conversation_id: z.string().uuid(), agent_run_id: z.string().uuid(), type: z.enum(["message.delta", "message.completed", "tool.started", "tool.completed", "tool.failed", "approval.required", "run.completed", "run.failed", "run.cancelled"]), payload: z.record(z.string(), z.unknown()), tool_call_id: z.string().nullable().optional() });
export type ConversationEvent = z.infer<typeof eventSchema>;
export type Projection = { conversationId: string; seq: number; seen: Set<string>; activeRuns: Set<string>; streams: Record<string, string>; tools: Record<string, { seq: number; runId: string; name: string; status: string; detail: unknown }> };
export type Action = { type: "snapshot"; snapshot: Snapshot } | { type: "events"; events: ConversationEvent[] } | { type: "reset" } | { type: "replay"; conversationId: string; events: ConversationEvent[] };
export const initialProjection: Projection = { conversationId: "", seq: 0, seen: new Set(), activeRuns: new Set(), streams: {}, tools: {} };
export function conversationReducer(state: Projection, action: Action): Projection {
  if (action.type === "reset") return initialProjection;
  if (action.type === "snapshot") {
    const same = state.conversationId === action.snapshot.id;
    if (same && action.snapshot.last_seq < state.seq) return state;
    const streams = same ? { ...state.streams } : {};
    for (const run of action.snapshot.runs) if (["completed", "failed", "cancelled"].includes(run.status)) delete streams[run.id];
    const tools = same ? state.tools : {};
    return { conversationId: action.snapshot.id, seq: action.snapshot.last_seq, seen: same ? state.seen : new Set(), activeRuns: new Set(action.snapshot.runs.filter(run => ["queued", "running"].includes(run.status)).map(run => run.id)), streams, tools };
  }
  if (action.type === "replay") {
    if (action.conversationId !== state.conversationId) return state;
    const tools = { ...state.tools };
    const seen = new Set(state.seen);
    const streams = { ...state.streams };
    for (const event of [...action.events].sort((a, b) => a.seq - b.seq)) {
      if (event.conversation_id !== state.conversationId) continue;
      if (event.seq <= state.seq && !seen.has(event.event_id)) {
        seen.add(event.event_id);
        if (state.activeRuns.has(event.agent_run_id) && event.type === "message.delta" && typeof event.payload.delta === "string") streams[event.agent_run_id] = (streams[event.agent_run_id] ?? "") + event.payload.delta;
        if (event.type === "message.completed" && event.payload.role !== "user") delete streams[event.agent_run_id];
      }
      if (event.conversation_id !== state.conversationId || !event.type.startsWith("tool.") || !event.tool_call_id) continue;
      const key = `${event.agent_run_id}:${event.tool_call_id}`;
      if (tools[key] && tools[key].seq >= event.seq) continue;
      tools[key] = { seq: event.seq, runId: event.agent_run_id, name: typeof event.payload.name === "string" ? event.payload.name : tools[key]?.name ?? "工具", status: event.type, detail: event.payload.result ?? event.payload.error ?? event.payload };
    }
    return { ...state, tools, streams, seen };
  }
  const next = { ...state, seen: new Set(state.seen), streams: { ...state.streams }, tools: { ...state.tools } };
  for (const event of [...action.events].sort((a, b) => a.seq - b.seq)) {
    if (event.conversation_id !== next.conversationId || next.seen.has(event.event_id) || event.seq <= next.seq) continue;
    if (event.seq !== next.seq + 1) break;
    next.seen.add(event.event_id); next.seq = event.seq;
    if (event.type === "message.delta" && typeof event.payload.delta === "string") next.streams[event.agent_run_id] = (next.streams[event.agent_run_id] ?? "") + event.payload.delta;
    if (event.type === "message.completed" && event.payload.role !== "user") delete next.streams[event.agent_run_id];
    if (event.type.startsWith("tool.") && event.tool_call_id) {
      const key = `${event.agent_run_id}:${event.tool_call_id}`;
      next.tools[key] = { seq: event.seq, runId: event.agent_run_id, name: typeof event.payload.name === "string" ? event.payload.name : next.tools[key]?.name ?? "工具", status: event.type, detail: event.payload.result ?? event.payload.error ?? event.payload };
    }
  }
  return next;
}
