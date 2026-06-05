export const ASK_USER_TOOL_NAME = "ask_user";
export const USER_INPUT_REQUEST_TYPE = "user_input_request";

export type UserQuestionOption = {
  id: string;
  label: string;
  description?: string;
};

export type UserQuestionFreeText = {
  enabled: true;
  label?: string;
  placeholder?: string;
  required?: boolean;
};

export type UserQuestion = {
  id: string;
  question: string;
  selection_mode: "single" | "multiple";
  required: boolean;
  options: UserQuestionOption[];
  free_text: UserQuestionFreeText;
};

export type UserQuestionAnswer = {
  question_id: string;
  selected_option_ids: string[];
  custom_text?: string;
};

export type UserAnswerResumePayload = {
  action: "answer";
  answers: UserQuestionAnswer[];
  summary: string;
};

export type UserInputRequestPayload = {
  tool_call_id?: string | null;
  interrupt_id?: string | null;
  title?: string;
  questions: UserQuestion[];
  submit_label?: string;
};

export type ParsedAskUserToolResult = {
  valid: boolean;
  summary: string;
  answers: UserQuestionAnswer[];
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function normalizeQuestionOption(value: unknown): UserQuestionOption | null {
  const record = asRecord(value);
  if (!record) {
    return null;
  }
  const id = String(record.id || "").trim();
  const label = String(record.label || "").trim();
  if (!id || !label) {
    return null;
  }
  return {
    id,
    label,
    description: String(record.description || ""),
  };
}

function normalizeFreeText(value: unknown): UserQuestionFreeText {
  const record = asRecord(value);
  return {
    enabled: true,
    label: String(record?.label || ""),
    placeholder: String(record?.placeholder || ""),
    required: Boolean(record?.required),
  };
}

export function normalizeUserQuestion(value: unknown): UserQuestion | null {
  const record = asRecord(value);
  if (!record) {
    return null;
  }
  const id = String(record.id || "").trim();
  const question = String(record.question || "").trim();
  if (!id || !question) {
    return null;
  }
  const options = Array.isArray(record.options)
    ? record.options
        .map(normalizeQuestionOption)
        .filter((option): option is UserQuestionOption => Boolean(option))
    : [];
  return {
    id,
    question,
    selection_mode: record.selection_mode === "multiple" ? "multiple" : "single",
    required: record.required !== false,
    options,
    free_text: normalizeFreeText(record.free_text),
  };
}

export function normalizeUserInputRequest(
  value: Record<string, unknown>,
): UserInputRequestPayload {
  const questions = Array.isArray(value.questions)
    ? value.questions
        .map(normalizeUserQuestion)
        .filter((question): question is UserQuestion => Boolean(question))
    : [];
  return {
    tool_call_id: String(value.tool_call_id || "") || null,
    interrupt_id: String(value.interrupt_id || "") || null,
    title: String(value.title || ""),
    questions,
    submit_label: String(value.submit_label || ""),
  };
}

export function summarizeUserInputAnswers(
  request: UserInputRequestPayload,
  answers: UserQuestionAnswer[],
): string {
  const byQuestion = new Map(answers.map((answer) => [answer.question_id, answer]));
  const lines = ["User answers:"];
  for (const question of request.questions) {
    const answer = byQuestion.get(question.id);
    const selectedLabels = (answer?.selected_option_ids || [])
      .map((optionId) => (
        question.options.find((option) => option.id === optionId)?.label || optionId
      ))
      .filter((label) => label.trim());
    const customText = String(answer?.custom_text || "").trim();
    const values = [...selectedLabels, customText].filter((value) => value.trim());
    lines.push(`- ${question.question}: ${values.length ? values.join(", ") : "No answer"}`);
  }
  return lines.join("\n");
}

export function parseAskUserToolResult(result: unknown): ParsedAskUserToolResult {
  let value = result;
  if (typeof result === "string") {
    try {
      value = JSON.parse(result);
    } catch {
      return { valid: false, summary: "", answers: [] };
    }
  }
  const record = asRecord(value);
  if (!record) {
    return { valid: false, summary: "", answers: [] };
  }
  if (record.type !== "ask_user_answer") {
    return { valid: false, summary: "", answers: [] };
  }
  const rawAnswers = Array.isArray(record.answers) ? record.answers : [];
  const answers = rawAnswers
    .map((item): UserQuestionAnswer | null => {
      const answer = asRecord(item);
      if (!answer) {
        return null;
      }
      const questionId = String(answer.question_id || "").trim();
      if (!questionId) {
        return null;
      }
      const selectedOptionIds = Array.isArray(answer.selected_option_ids)
        ? answer.selected_option_ids.map((optionId) => String(optionId || "").trim()).filter(Boolean)
        : [];
      return {
        question_id: questionId,
        selected_option_ids: selectedOptionIds,
        custom_text: String(answer.custom_text || ""),
      };
    })
    .filter((answer): answer is UserQuestionAnswer => Boolean(answer));
  return {
    valid: true,
    summary: String(record.summary || ""),
    answers,
  };
}
