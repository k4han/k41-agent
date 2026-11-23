import { createResource, createSignal, For, Show } from "solid-js";
import { Button } from "@/components/Button";
import { PlanReviewView, createTranscriptUserInputRequest } from "@/components/Transcript";
import { UserInputRequestCard, type UserInputRequestSubmitPayload } from "@/components/UserInputRequestCard";
import { asyncInputRequest, liveAsyncTask, type AsyncTask } from "@/lib/asyncSubagents";
import { fetchAgentCards } from "@/lib/agents";

export function AsyncTaskControls(props: {
  task: AsyncTask | undefined;
  busy: boolean;
  error: string;
  perform: (action: string, payload?: unknown) => Promise<boolean>;
}) {
  const [instructions, setInstructions] = createSignal("");
  const hasPlan = () => props.task?.interrupts?.some((item) => item.value.type === "plan_review") || false;
  const [cards] = createResource(hasPlan, async () => (await fetchAgentCards()).cards);
  const inputRequest = (interrupt: { id: string; value: Record<string, unknown> }) => {
    const request = asyncInputRequest(interrupt);
    return createTranscriptUserInputRequest({ toolCallId: request.tool_call_id, interruptId: request.interrupt_id,
      title: request.title, questions: request.questions, submitLabel: request.submit_label, status: "pending" });
  };
  const resume = (interruptId: string, payload: unknown) => props.perform("resume", {
    run_id: props.task?.run_id, interrupt_id: interruptId, payload,
  });
  const answer = (interruptId: string, payload: UserInputRequestSubmitPayload) => {
    const permission = payload.answers.find((item) => item.question_id.startsWith("permission:"));
    void resume(interruptId, permission ? { action: "permission", request_id: permission.question_id.slice(11),
      decision: permission.selected_option_ids[0] } : { action: "answer", answers: payload.answers, summary: payload.summary });
  };
  const update = async () => {
    if (await props.perform("updates", { instructions: instructions().trim(), request_id: crypto.randomUUID() })) setInstructions("");
  };
  return <section class="async-task-controls" aria-label="Background task controls">
    <Show when={props.task?.queued_updates}><p>{props.task!.queued_updates} follow-up(s) queued</p></Show>
    <Show when={props.task?.error}><p role="alert">{props.task!.error}</p></Show>
    <Show when={props.error}><p role="alert">{props.error}</p></Show>
    <Show when={props.task?.status === "waiting"}>
      <For each={props.task?.interrupts}>{(interrupt) => <Show when={interrupt.value.type === "browser_handoff_request"} fallback={
        <Show when={interrupt.value.type === "plan_review"} fallback={
          <UserInputRequestCard request={inputRequest(interrupt)}
            disabled={props.busy} onSubmit={(payload) => answer(interrupt.id, payload)} />
        }>
          <PlanReviewView plan={String(interrupt.value.plan || "")} status="pending" agents={cards() || []}
            activeAgentName={props.task?.agent_name} actionsDisabled={props.busy}
            onApprove={(payload) => void resume(interrupt.id, { action: "approve", target_agent: payload.targetAgent })}
            onRevise={(payload) => void resume(interrupt.id, { action: "revise", feedback: payload.feedback })} />
        </Show>
      }>
        <p>{String(interrupt.value.reason || "Complete the manual step in the browser.")}</p>
        <Button size="sm" disabled={props.busy} onClick={() => window.dispatchEvent(new CustomEvent("k41-browser-handoff", {
          detail: { threadId: props.task?.parent_thread_id, browserThreadId: props.task?.thread_id },
        }))}>Open browser</Button>
      </Show>}</For>
    </Show>
    <Show when={props.task && props.task.status !== "cancelled"}>
      <label class="field">Follow-up instructions<textarea class="input" value={instructions()} disabled={props.busy}
        onInput={(event) => setInstructions(event.currentTarget.value)} rows={2} /></label>
      <div class="async-task-actions">
        <Button size="sm" variant="primary" disabled={props.busy || !instructions().trim()} onClick={() => void update()}>Queue instructions</Button>
        <Show when={liveAsyncTask(props.task!.status)}><Button size="sm" variant="danger" disabled={props.busy}
          onClick={() => void props.perform("cancel")}>Cancel task</Button></Show>
      </div>
    </Show>
  </section>;
}
