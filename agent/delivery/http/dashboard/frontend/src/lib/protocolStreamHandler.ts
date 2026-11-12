import type { ContentBlockDelta, Event as ProtocolEvent } from "@langchain/protocol";
import type { SubagentActivity, TranscriptItem } from "@/components/Transcript";
import {
  applyApplicationEvent,
  mergeToolActivity,
  settleStreamThinking,
  type AssistantIdRef,
  type StreamCallbacks,
  type StreamedRef,
  type StreamThreadIdRef,
} from "@/lib/chatStreamHandler";

type Scope = {
  refs: AssistantIdRef;
  streamed: StreamedRef;
  callbacks: StreamCallbacks;
  tools: Set<string>;
  items: Array<TranscriptItem & { id: number }>;
  descriptor?: SubagentActivity;
  currentMessage: string;
  messages: Map<string, { refs: AssistantIdRef; streamed: StreamedRef }>;
  toolBlocks: Map<string, { id: string; name: string; args: string }>;
  outputDeltas: Map<string, unknown[]>;
  commit: () => void;
};

export type ProtocolState = {
  runId: string;
  seq: number;
  scopes: Map<string, Scope>;
};

const object = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" ? value as Record<string, unknown> : {};

function childScope(parent: Scope, descriptor: SubagentActivity, thread: StreamThreadIdRef): Scope {
  let nextId = -1;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const scope: Scope = {
    refs: { id: null }, streamed: { received: false }, callbacks: parent.callbacks,
    tools: new Set(), items: [], descriptor, currentMessage: "", messages: new Map(),
    toolBlocks: new Map(), outputDeltas: new Map(),
    commit: () => {
      if (timer !== undefined) clearTimeout(timer);
      timer = undefined;
      parent.callbacks.updateToolActivity?.(descriptor.tool_call_id, {
        subagent: { ...scope.descriptor!, items: scope.items.map((item) => ({ ...item })) },
      }, thread.id);
    },
  };
  const schedule = () => { timer ??= setTimeout(scope.commit, 50); };
  const patch = (id: number, update: (item: TranscriptItem & { id: number }) => void) => {
    const item = scope.items.find((row) => row.id === id);
    if (item) update(item);
    schedule();
  };
  scope.callbacks = {
    ...parent.callbacks,
    appendItem: (item) => {
      const id = nextId--;
      scope.items.push({ ...item, id });
      schedule();
      return id;
    },
    updateMessage: (id, text) => patch(id, (item) => {
      if (item.type === "message" || item.type === "thinking") item.text += text;
    }),
    replaceMessage: (id, text) => patch(id, (item) => { if (item.type === "message") item.text = text; }),
    finishThinking: (id, durationMs) => patch(id, (item) => { if (item.type === "thinking") item.durationMs = durationMs; }),
    removeItem: (id) => { scope.items = scope.items.filter((item) => item.id !== id); schedule(); },
    updateToolResult: (id, name, result) => {
      const tool = scope.items.find((item) => item.type === "tool" && item.tool_call_id === id);
      if (tool?.type === "tool") Object.assign(tool, { result, resultReceived: true });
      else scope.items.push({ id: nextId--, type: "tool", tool_call_id: id, name, args: null, result, resultReceived: true });
      schedule();
    },
    updateToolActivity: (id, changes) => {
      const tool = scope.items.find((item) => item.type === "tool" && item.tool_call_id === id);
      if (tool?.type === "tool") Object.assign(tool, mergeToolActivity(tool, changes));
      schedule();
    },
    onContextUsage: undefined,
    onThreadCreated: () => {}, onThreadTitle: undefined,
  };
  return scope;
}

export function handleProtocolEvent(
  input: Record<string, unknown>, refs: AssistantIdRef, streamed: StreamedRef,
  thread: StreamThreadIdRef, callbacks: StreamCallbacks,
): void {
  const event = input as unknown as Omit<ProtocolEvent, "method"> & { method: string };
  const params = object(event.params);
  const namespace = Array.isArray(params.namespace) ? params.namespace as string[] : [];
  const eventId = String(input.event_id || "");
  const runId = eventId.split(":")[0];
  let state = refs.protocol;
  if (!state || (runId && state.runId && state.runId !== runId)) {
    const root: Scope = { refs, streamed, callbacks, tools: new Set(), items: [],
      currentMessage: "", messages: new Map(), toolBlocks: new Map(), outputDeltas: new Map(), commit: () => {} };
    refs.protocol = state = { runId, seq: 0, scopes: new Map([["[]", root]]) };
  }
  if (typeof input.seq === "number") {
    if (input.seq <= state.seq) return;
    state.seq = input.seq;
  }
  let scope = state.scopes.get(JSON.stringify(namespace));
  for (let depth = namespace.length - 1; !scope && depth >= 0; depth--) {
    scope = state.scopes.get(JSON.stringify(namespace.slice(0, depth)));
  }
  scope ??= state.scopes.get("[]")!;
  const dispatch = (data: Record<string, unknown>, target = scope!) => {
    if (data.type === "tool_call") {
      const id = String(data.id || "");
      if (target.tools.has(id)) {
        target.callbacks.updateToolActivity?.(id, { args: data.args, name: String(data.name || "unknown") }, thread.id);
        return;
      }
      target.tools.add(id);
    }
    applyApplicationEvent(data, target.refs, target.streamed, thread, target.callbacks);
  };
  const ensureTool = (target: Scope, id: string, name: string, args: unknown) => {
    dispatch({ type: "tool_call", id, name, args }, target);
  };
  if (event.method === "lifecycle") {
    const data = object(params.data);
    if (namespace.length && data.delegation_id) {
      const key = JSON.stringify(namespace);
      let child = state.scopes.get(key);
      if (!child) {
        const parent = state.scopes.get(JSON.stringify(namespace.slice(0, -1))) || scope;
        const id = String(data.tool_call_id || "");
        ensureTool(parent, id, "delegate_task", { sub_agent: data.agent_name });
        child = childScope(parent, {
          delegation_id: String(data.delegation_id), agent_name: String(data.agent_name || data.graph_name),
          parent_thread_id: namespace.length === 1 ? thread.id : String(parent.descriptor?.thread_id || thread.id),
          thread_id: String(data.thread_id || ""), tool_call_id: id,
          status: String(data.event || "started"), items: [],
        }, thread);
        state.scopes.set(key, child);
      }
      child.descriptor = { ...child.descriptor!, status: String(data.event), error: data.error ? String(data.error) : undefined };
      child.commit();
    } else if (!namespace.length) {
      if (data.event === "started" && data.thread_id && (!thread.id || thread.id.startsWith("__pending__"))) {
        callbacks.onThreadCreated(String(data.thread_id), thread);
      }
      if (["completed", "interrupted", "failed"].includes(String(data.event))) {
        for (const child of Array.from(state.scopes.values()).reverse()) {
          settleStreamThinking(child.refs, thread, child.callbacks);
          if (child.descriptor && ["started", "running"].includes(child.descriptor.status)) {
            child.descriptor = { ...child.descriptor, status: data.event === "failed" ? "failed" : "completed" };
          }
          child.commit();
        }
      }
    }
    return;
  }
  if (event.method === "messages") {
    const [raw, rawMetadata] = Array.isArray(params.data) ? params.data : [params.data, {}];
    const data = object(raw), metadata = object(rawMetadata);
    const id = String(metadata.run_id || data.id || scope.currentMessage);
    if (data.event === "message-start") {
      scope.currentMessage = id;
      if (!scope.messages.has(id)) scope.messages.set(id, {
        refs: { id: scope.messages.size ? null : scope.refs.id }, streamed: { received: false },
      });
      if (data.id) scope.messages.set(String(data.id), scope.messages.get(id)!);
    }
    // Text and reasoning use the application's projection, which also handles
    // inline thinking tags, provider quirks, and non-streaming model results.
    // Native blocks supply invocation boundaries and live tool-call arguments.
    if (data.event === "content-block-start" || data.event === "content-block-finish") {
      const content = object(data.content);
      if (content.type === "tool_call" && content.id) {
        const call = { id: String(content.id), name: String(content.name || "unknown"),
          args: typeof content.args === "string" ? content.args : "" };
        scope.toolBlocks.set(`${id}:${data.index}`, call);
        ensureTool(scope, call.id, call.name, content.args || "");
      }
    }
    if (data.event === "content-block-delta") {
      const delta = object(data.delta) as Partial<ContentBlockDelta> & Record<string, unknown>;
      if (delta.type === "tool-call-delta") {
        const call = scope.toolBlocks.get(`${id}:${data.index}`);
        if (call) {
          call.args += String(delta.args || "");
          ensureTool(scope, call.id, call.name, call.args);
        }
      }
    }
    return;
  }
  if (event.method === "tools") {
    const data = object(params.data), id = String(data.tool_call_id || "");
    if (data.event === "tool-started") {
      ensureTool(scope, id, String(data.tool_name || "unknown"), data.input || {});
      scope.callbacks.updateToolActivity?.(id, { executionStatus: "running" }, thread.id);
    } else if (data.event === "tool-output-delta") {
      const deltas = scope.outputDeltas.get(id) || [];
      deltas.push(data.delta);
      scope.outputDeltas.set(id, deltas);
      scope.callbacks.updateToolActivity?.(id, { outputDelta: deltas.every((part) => typeof part === "string")
        ? deltas.join("") : [...deltas] }, thread.id);
    } else if (data.event === "tool-finished" || data.event === "tool-error") {
      const output = object(data.output);
      scope.callbacks.updateToolActivity?.(id, {
        executionStatus: data.event === "tool-error" || output.status === "error" ? "failed" : "completed",
      }, thread.id);
    }
    return;
  }
  if (event.method === "custom:k41") {
    const data = object(params.data);
    if (data.type === "run_state") {
      if (scope.descriptor) {
        scope.descriptor = { ...scope.descriptor, checkpoint_id: String(data.checkpoint_id || "") };
        scope.commit();
      }
      return;
    }
    if (data.projection === "ui" || data.type === "thread_created") {
      const message = scope.messages.get(String(data.message_id || scope.currentMessage));
      if (message && ["message", "thinking", "final"].includes(String(data.type))) {
        applyApplicationEvent(data, message.refs, message.streamed, thread, scope.callbacks);
        Object.assign(scope.refs, { id: message.refs.id, thinkingId: message.refs.thinkingId,
          thinkingStartedAt: message.refs.thinkingStartedAt });
        scope.streamed.received = message.streamed.received;
      } else dispatch(data);
      if (data.type === "tool_result" && data.subagent) {
        scope.callbacks.updateToolActivity?.(String(data.tool_call_id), { subagent: data.subagent as SubagentActivity }, thread.id);
      }
    }
  }
}
