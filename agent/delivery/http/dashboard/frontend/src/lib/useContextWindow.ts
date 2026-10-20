import { createEffect, createMemo, createSignal, untrack } from "solid-js";

import type { ContextWindowData } from "@/components/ContextWindowIndicator";
import { apiFetch } from "@/lib/api";
import { getOrCreateStreamSignals, persistedStreams, type ContextUsage } from "@/lib/chatStreamStore";
import { resolveModelAndProvider } from "@/lib/modelSelection";
import type { AgentCard, AgentChatPayload, ThreadUsagePayload } from "@/types";
import { CONTEXT_CATEGORIES, parseContextBreakdown, type ContextCategory } from "@/types";

const CATEGORY_LABELS: Record<ContextCategory, string> = {
  system_prompt: "System prompt",
  system_tools: "System tools",
  skills: "Skills",
  subagents: "Subagents",
  user_messages: "User messages",
  agent_responses: "Agent responses",
  tool_calls: "Tool calls",
};

const isTokenCount = (value: unknown): value is number =>
  typeof value === "number" && Number.isSafeInteger(value) && value >= 0;

const contextTimestamp = (value: string | null | undefined): number =>
  typeof value === "string" ? Date.parse(value) : NaN;

export interface UseContextWindowParams {
  getCurrentThreadId: () => string;
  getStreaming: () => boolean;
  getSelectedCard: () => AgentCard | undefined;
  getData: () => AgentChatPayload | undefined;
  getProvider: () => string;
  getModel: () => string;
}

export function useContextWindow(params: UseContextWindowParams) {
  const {
    getCurrentThreadId,
    getStreaming,
    getSelectedCard,
    getData,
    getProvider,
    getModel,
  } = params;

  const [threadUsage, setThreadUsage] = createSignal<ThreadUsagePayload | null>(null);
  let usageRequestId = 0;
  const [localReportedUsage, setLocalReportedUsage] = createSignal<{
    threadId: string;
    usage: ContextUsage;
  } | null>(null);

  const getReportedUsage = (threadId: string) => {
    const persisted = persistedStreams.get(threadId)?.reportedContextUsage[0]();
    const local = localReportedUsage();
    return persisted ?? (local?.threadId === threadId ? local.usage : null);
  };

  const updateContextUsage = (usage: ContextUsage, threadId: string) => {
    getOrCreateStreamSignals(threadId).reportedContextUsage[1](usage);
    if (threadId === getCurrentThreadId()) {
      setLocalReportedUsage({ threadId, usage });
    }
  };

  const clearContextUsage = (threadId: string) => {
    persistedStreams.get(threadId)?.reportedContextUsage[1](null);
    if (threadId === getCurrentThreadId()) {
      setLocalReportedUsage(null);
    }
    const current = threadUsage();
    if (current?.thread_id === threadId) {
      setThreadUsage(null);
    }
  };

  const updateCompactedContextUsage = (
    tokens: number | undefined, threadId: string, breakdown?: unknown,
    details?: Pick<ContextUsage, "context_updated_at" | "context_window" | "reserved_tokens">,
  ) => {
    const contextWindow = contextWindowData().maxTokens;
    clearContextUsage(threadId);
    if (typeof tokens === "number" && Number.isSafeInteger(tokens) && tokens >= 0) {
      const usage: ContextUsage = {
        current_context_tokens: tokens,
        context_window: contextWindow,
        estimated: true,
        context_breakdown: parseContextBreakdown(breakdown),
        ...details,
      };
      // Creating an empty stream here would hide the loaded transcript.
      persistedStreams.get(threadId)?.reportedContextUsage[1](usage);
      if (threadId === getCurrentThreadId()) {
        setLocalReportedUsage({ threadId, usage });
      }
    }
  };

  const fetchThreadUsage = async (threadId: string) => {
    const requestId = ++usageRequestId;
    if (!threadId) {
      setThreadUsage(null);
      return;
    }
    const reportAtStart = getReportedUsage(threadId);
    try {
      const data = await apiFetch<ThreadUsagePayload>(
        `/dashboard-api/usage/thread/${encodeURIComponent(threadId)}`,
      );
      if (requestId !== usageRequestId || data.thread_id !== threadId
        || threadId !== getCurrentThreadId() || getStreaming()
        || getReportedUsage(threadId) !== reportAtStart) {
        return;
      }
      const incomingTimestamp = contextTimestamp(data.context_updated_at);
      const reportedTimestamp = contextTimestamp(reportAtStart?.context_updated_at);
      if (reportAtStart) {
        // Only replace a live snapshot when the server proves it is at least as recent.
        // Older servers can still fill in an unversioned compaction estimate.
        const versioned = Number.isFinite(incomingTimestamp) && Number.isFinite(reportedTimestamp);
        const legacyCompaction = reportAtStart.estimated && !Number.isFinite(reportedTimestamp);
        if (versioned ? incomingTimestamp < reportedTimestamp : !legacyCompaction) return;
      }
      setThreadUsage(data);
      if (!reportAtStart) return;
      const estimated = data.context_estimated === true && isTokenCount(data.current_context_tokens);
      const hasUsage = !estimated && data.has_context_usage !== false && isTokenCount(data.latest_input_tokens);
      const outputTokens = isTokenCount(data.latest_output_tokens) ? data.latest_output_tokens : 0;
      const usage: ContextUsage = {
        current_context_tokens: estimated ? data.current_context_tokens!
          : hasUsage ? data.latest_input_tokens! + outputTokens : 0,
        context_window: isTokenCount(data.context_window) && data.context_window > 0
          ? data.context_window : reportAtStart.context_window,
        has_context_usage: hasUsage,
        estimated,
        context_updated_at: data.context_updated_at ?? undefined,
        reserved_tokens: isTokenCount(data.reserved_tokens) ? data.reserved_tokens : undefined,
        provider: data.latest_provider,
        model: data.latest_model,
        pool_name: data.pool_name,
        input_tokens: hasUsage ? data.latest_input_tokens : undefined,
        output_tokens: hasUsage ? outputTokens : undefined,
        cache_read_tokens: hasUsage && isTokenCount(data.latest_cache_read_tokens)
          ? data.latest_cache_read_tokens : undefined,
        context_breakdown: parseContextBreakdown(data.context_breakdown),
      };
      persistedStreams.get(threadId)?.reportedContextUsage[1](usage);
      setLocalReportedUsage({ threadId, usage });
    } catch (err) {
      console.error("Failed to fetch thread usage:", err);
    }
  };

  createEffect(() => {
    const threadId = getCurrentThreadId();
    const isStreaming = getStreaming();
    if (threadId && !isStreaming) {
      untrack(() => void fetchThreadUsage(threadId));
    } else if (!threadId) {
      setThreadUsage(null);
      setLocalReportedUsage(null);
    }
  });

  const contextWindowData = createMemo<ContextWindowData>(() => {
    const card = getSelectedCard();
    const threadId = getCurrentThreadId();
    const fetchedUsage = threadUsage();
    const usage = fetchedUsage?.thread_id === threadId ? fetchedUsage : null;
    const reportedUsage = getReportedUsage(threadId);
    const payload = getData();

    let maxTokens = 128000;
    let catalogMaxTokens: number | null = null;
    let selected: { provider: string; model: string } | undefined;
    if (payload) {
      const activeProvider = getProvider() || card?.provider || "default";
      const activeModel = getModel() || card?.model || "";
      selected = resolveModelAndProvider(
        activeProvider, activeModel, payload.default_provider, payload.default_model, payload.model_catalogs || [],
      );
      const catalog = payload.model_catalogs?.find((c) => c.provider === selected?.provider);
      const modelOption = catalog?.models?.find((m) => m.id === selected?.model);
      if (modelOption && isTokenCount(modelOption.context_window) && modelOption.context_window > 0) {
        maxTokens = modelOption.context_window;
        catalogMaxTokens = modelOption.context_window;
      }
    }

    // Prefer the catalog for the currently selected model. A stream report
    // carries the window of the model used for that call, which goes stale
    // as soon as the user switches models before the next call.
    const matchesPool = selected?.provider === "@pool"
      && selected.model === (reportedUsage ? reportedUsage.pool_name : usage?.pool_name);
    if (catalogMaxTokens === null || matchesPool) {
      const window = reportedUsage ? reportedUsage.context_window : usage?.context_window;
      if (isTokenCount(window) && window > 0) maxTokens = window;
    }

    const estimatedTokens = reportedUsage
      ? reportedUsage.estimated === true ? reportedUsage.current_context_tokens : undefined
      : usage?.context_estimated === true && isTokenCount(usage.current_context_tokens)
        ? usage.current_context_tokens : undefined;
    const reportedTokens = reportedUsage
      ? reportedUsage.has_context_usage !== false && !reportedUsage.estimated
        ? reportedUsage.current_context_tokens : undefined
      : usage?.has_context_usage !== false && !usage?.context_estimated && isTokenCount(usage?.latest_input_tokens)
        ? usage.latest_input_tokens + (isTokenCount(usage.latest_output_tokens) ? usage.latest_output_tokens : 0) : undefined;
    const estimated = estimatedTokens !== undefined;
    const hasReportedUsage = reportedTokens !== undefined;
    const inputTokens = hasReportedUsage
      ? (reportedUsage ? reportedUsage.input_tokens ?? reportedUsage.current_context_tokens : usage?.latest_input_tokens ?? 0) : 0;
    const outputTokens = hasReportedUsage
      ? (reportedUsage ? reportedUsage.output_tokens ?? 0 : usage?.latest_output_tokens ?? 0) : 0;
    const reportedCacheRead = reportedUsage ? reportedUsage.cache_read_tokens : usage?.latest_cache_read_tokens;
    const cacheReadTokens = hasReportedUsage && isTokenCount(reportedCacheRead) && reportedCacheRead <= inputTokens
      ? reportedCacheRead : null;
    const totalTokens = reportedTokens ?? estimatedTokens ?? 0;

    const totalPercent = maxTokens > 0 ? Math.min(100, (totalTokens / maxTokens) * 100) : 0;

    const capacityWindow = reportedUsage ? reportedUsage.context_window : usage?.context_window;
    const capacityProvider = reportedUsage ? reportedUsage.provider : usage?.latest_provider;
    const capacityModel = reportedUsage ? reportedUsage.model : usage?.latest_model;
    const sameModel = matchesPool || !selected || ((!capacityProvider || selected.provider === capacityProvider)
      && (!capacityModel || selected.model === capacityModel));
    const reportedReserve = reportedUsage ? reportedUsage.reserved_tokens : usage?.reserved_tokens;
    const reservedTokens = Math.min(maxTokens, sameModel && capacityWindow === maxTokens
      && isTokenCount(reportedReserve) && reportedReserve > 0
      ? reportedReserve : Math.min(4096, Math.max(1, Math.floor(maxTokens / 8))));
    const reservedPercent = maxTokens > 0 ? (reservedTokens / maxTokens) * 100 : 0;

    // The breakdown must describe the same model call or compaction snapshot.
    const breakdown = hasReportedUsage || estimated ? parseContextBreakdown(
      reportedUsage ? reportedUsage.context_breakdown : usage?.context_breakdown,
    ) : undefined;

    const trimDecimals = (num: number): string => num.toFixed(2).replace(/\.?0+$/, "");

    const formatNumber = (num: number): string => {
      if (num >= 1000000) {
        return trimDecimals(num / 1000000) + "M";
      }
      if (num >= 1000) {
        return trimDecimals(num / 1000) + "K";
      }
      return num.toString();
    };

    const categories = CONTEXT_CATEGORIES.map((key) => {
      const tokens = breakdown?.[key] ?? null;
      const percent = tokens !== null && maxTokens > 0 ? (tokens / maxTokens) * 100 : 0;
      return {
        key,
        label: CATEGORY_LABELS[key],
        tokens,
        formattedValue: tokens === null ? "—" : `${tokens < 1000 ? tokens.toString() : `${trimDecimals(tokens / 1000)}k`} tokens (${percent.toFixed(1)}%)`,
      };
    });

    return {
      maxTokens,
      totalTokens,
      inputTokens,
      outputTokens,
      cacheReadTokens,
      formattedCacheHit: cacheReadTokens === null ? "—"
        : `${formatNumber(cacheReadTokens)} tokens (${(inputTokens > 0 ? cacheReadTokens / inputTokens * 100 : 0).toFixed(1)}%)`,
      totalPercent,
      reservedPercent,
      categories,
      formattedUsed: estimated ? `~${formatNumber(totalTokens)}`
        : hasReportedUsage ? formatNumber(totalTokens) : "—",
      formattedMax: formatNumber(maxTokens),
      hasReportedUsage,
      estimated,
    };
  });

  return {
    contextWindowData,
    refreshThreadUsage: fetchThreadUsage,
    updateContextUsage,
    clearContextUsage,
    updateCompactedContextUsage,
  };
}
