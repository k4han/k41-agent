import { Check, ChevronLeft, ChevronRight, Circle, Square } from "lucide-solid";
import { createEffect, createMemo, createSignal, For, Show } from "solid-js";

import type { TranscriptUserInputRequest } from "@/components/Transcript";
import {
  summarizeUserInputAnswers,
  type UserQuestion,
  type UserQuestionAnswer,
} from "@/lib/userInputRequest";

export type UserInputRequestSubmitPayload = {
  toolCallId?: string | null;
  answers: UserQuestionAnswer[];
  summary: string;
};

export function UserInputRequestCard(props: {
  request: TranscriptUserInputRequest;
  disabled?: boolean;
  onSubmit: (payload: UserInputRequestSubmitPayload) => void;
}) {
  const [selectedByQuestion, setSelectedByQuestion] = createSignal<Record<string, string[]>>({});
  const [textByQuestion, setTextByQuestion] = createSignal<Record<string, string>>({});
  const [currentIndex, setCurrentIndex] = createSignal(0);

  createEffect(() => {
    const requestKey = props.request.tool_call_id || props.request.interrupt_id || "";
    props.request.questions.length;
    setSelectedByQuestion({});
    setTextByQuestion({});
    setCurrentIndex(0);
    void requestKey;
  });

  const questions = () => props.request.questions;
  const currentQuestion = createMemo(() => (
    questions()[Math.min(currentIndex(), Math.max(questions().length - 1, 0))]
  ));

  const selectedIds = (questionId: string) => selectedByQuestion()[questionId] || [];
  const customText = (questionId: string) => textByQuestion()[questionId] || "";

  const toggleOption = (question: UserQuestion, optionId: string) => {
    if (props.disabled) {
      return;
    }
    setSelectedByQuestion((current) => {
      const currentIds = current[question.id] || [];
      const selected = currentIds.includes(optionId);
      const nextIds = question.selection_mode === "single"
        ? (selected ? [] : [optionId])
        : (selected
            ? currentIds.filter((id) => id !== optionId)
            : [...currentIds, optionId]);
      return { ...current, [question.id]: nextIds };
    });
    if (question.selection_mode === "single" && currentIndex() < questions().length - 1) {
      window.setTimeout(() => {
        if (questionAnswered(question)) {
          setCurrentIndex((current) => Math.min(current + 1, questions().length - 1));
        }
      }, 0);
    }
  };

  const setQuestionText = (questionId: string, value: string) => {
    setTextByQuestion((current) => ({ ...current, [questionId]: value }));
  };

  const questionAnswered = (question: UserQuestion) => {
    const hasSelection = selectedIds(question.id).length > 0;
    const hasText = customText(question.id).trim().length > 0;
    if (question.free_text?.required && !hasText) {
      return false;
    }
    if (!question.required) {
      return true;
    }
    return hasSelection || hasText;
  };

  const canSubmit = createMemo(() => (
    props.request.questions.length > 0
    && props.request.questions.every(questionAnswered)
  ));
  const canContinue = createMemo(() => {
    const question = currentQuestion();
    return question ? questionAnswered(question) : false;
  });
  const isLastQuestion = createMemo(() => currentIndex() >= questions().length - 1);

  const buildAnswers = (): UserQuestionAnswer[] =>
    props.request.questions.map((question) => ({
      question_id: question.id,
      selected_option_ids: selectedIds(question.id),
      custom_text: customText(question.id).trim(),
    }));

  const submit = () => {
    if (props.disabled || !canSubmit()) {
      return;
    }
    const answers = buildAnswers();
    props.onSubmit({
      toolCallId: props.request.tool_call_id,
      answers,
      summary: summarizeUserInputAnswers(
        {
          tool_call_id: props.request.tool_call_id,
          interrupt_id: props.request.interrupt_id,
          title: props.request.title,
          questions: props.request.questions,
          submit_label: props.request.submit_label,
        },
        answers,
      ),
    });
  };

  const goBack = () => {
    setCurrentIndex((current) => Math.max(0, current - 1));
  };

  const goNext = () => {
    if (!canContinue()) {
      return;
    }
    setCurrentIndex((current) => Math.min(current + 1, questions().length - 1));
  };

  return (
    <section class="user-input-request-card">
      <div class="user-input-request-header">
        <div>
          <div class="user-input-request-title">
            {props.request.title || "Input requested"}
          </div>
          <div class="user-input-request-meta">
            {Math.min(currentIndex() + 1, props.request.questions.length)}/{props.request.questions.length}
          </div>
        </div>
      </div>
      <div class="user-input-request-questions">
        <Show when={currentQuestion()}>
          {(question) => (
            <div class="user-input-question">
              <div class="user-input-question-title">
                <span>{currentIndex() + 1}.</span>
                <span>{question().question}</span>
                <Show when={!question().required}>
                  <span class="user-input-question-optional">Optional</span>
                </Show>
              </div>
              <Show when={question().options.length > 0}>
                <div class="user-input-options">
                  <For each={question().options}>
                    {(option) => {
                      const isSelected = () => selectedIds(question().id).includes(option.id);
                      return (
                        <button
                          class={`user-input-option ${isSelected() ? "selected" : ""}`}
                          type="button"
                          onClick={() => toggleOption(question(), option.id)}
                          disabled={props.disabled}
                          aria-pressed={isSelected()}
                        >
                          <span class="user-input-option-icon">
                            <Show
                              when={isSelected()}
                              fallback={question().selection_mode === "single" ? <Circle size={16} /> : <Square size={16} />}
                            >
                              <Check size={16} />
                            </Show>
                          </span>
                          <span class="user-input-option-copy">
                            <span class="user-input-option-label">{option.label}</span>
                            <Show when={option.description}>
                              <span class="user-input-option-description">{option.description}</span>
                            </Show>
                          </span>
                        </button>
                      );
                    }}
                  </For>
                </div>
              </Show>
              <Show when={question().free_text?.enabled}>
                <label class="user-input-free-text">
                  <span>
                    {question().free_text?.label || "Custom answer"}
                    <Show when={question().free_text?.required}>
                      <span class="user-input-required">Required</span>
                    </Show>
                  </span>
                  <textarea
                    rows={2}
                    value={customText(question().id)}
                    disabled={props.disabled}
                    placeholder={question().free_text?.placeholder || ""}
                    onInput={(event) => setQuestionText(question().id, event.currentTarget.value)}
                  />
                </label>
              </Show>
            </div>
          )}
        </Show>
      </div>
      <div class="user-input-request-actions">
        <button
          class="btn user-input-step-btn"
          type="button"
          onClick={goBack}
          disabled={props.disabled || currentIndex() <= 0}
        >
          <ChevronLeft size={15} />
          <span>Back</span>
        </button>
        <Show
          when={isLastQuestion()}
          fallback={
            <button
              class="btn primary user-input-submit-btn"
              type="button"
              onClick={goNext}
              disabled={props.disabled || !canContinue()}
            >
              <span>Next</span>
              <ChevronRight size={15} />
            </button>
          }
        >
        <button
          class="btn primary user-input-submit-btn"
          type="button"
          onClick={submit}
          disabled={props.disabled || !canSubmit()}
        >
          <Check size={15} />
          <span>{props.request.submit_label || "Submit answer"}</span>
        </button>
        </Show>
      </div>
    </section>
  );
}
