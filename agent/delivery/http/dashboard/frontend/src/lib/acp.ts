// Shared rules for agent cards that run an external agent over ACP.

export const ACP_GRAPH_TYPE = "acp_agent";
export const ACP_PERMISSION_QUESTION_PREFIX = "acp_permission:";

type CardLike = { graph_type?: string | null } | null | undefined;

export function isAcpCard(card: CardLike): boolean {
  return card?.graph_type === ACP_GRAPH_TYPE;
}

/** A started conversation stays on one side of the ACP boundary. */
export function selectableAgentCards<T extends { graph_type?: string | null }>(
  cards: T[],
  selected: CardLike,
  hasThread: boolean,
): T[] {
  if (!hasThread || !selected) return cards;
  const selectedIsAcp = isAcpCard(selected);
  return cards.filter((card) => isAcpCard(card) === selectedIsAcp);
}

export function acpPermissionRequestId(questionId: string): string | null {
  return questionId.startsWith(ACP_PERMISSION_QUESTION_PREFIX)
    ? questionId.slice(ACP_PERMISSION_QUESTION_PREFIX.length)
    : null;
}

export type AcpConfigOption = {
  id: string;
  name: string;
  category: string;
  current_value: string | null;
  options: { value: string; name: string }[];
};

export type AcpOptionValues = Record<string, string>;

/** What the next turn uses per option: the chat choice, then the card, then the agent's own default. */
export function resolveAcpOptionValues(
  options: AcpConfigOption[],
  chosen: AcpOptionValues,
  card: AcpOptionValues,
): AcpOptionValues {
  const values: AcpOptionValues = {};
  for (const option of options) {
    // A remembered value can outlive the choices, for example a retired model.
    const value = [chosen[option.id], card[option.id], option.current_value]
      .find((candidate) => option.options.some((item) => item.value === candidate));
    if (value != null) values[option.id] = value;
  }
  return values;
}

const ACP_OPTIONS_STORAGE_KEY = "k41.dashboard.acpOptions";

function readAcpOptionStore(): Record<string, AcpOptionValues> {
  try {
    const value = JSON.parse(window.localStorage.getItem(ACP_OPTIONS_STORAGE_KEY) || "null");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

export function readAcpOptionChoices(cardName: string): AcpOptionValues {
  const stored = readAcpOptionStore()[cardName];
  if (!stored || typeof stored !== "object" || Array.isArray(stored)) return {};
  return Object.fromEntries(Object.entries(stored).filter((entry): entry is [string, string] => typeof entry[1] === "string"));
}

export function writeAcpOptionChoices(cardName: string, choices: AcpOptionValues): void {
  try {
    window.localStorage.setItem(ACP_OPTIONS_STORAGE_KEY, JSON.stringify({ ...readAcpOptionStore(), [cardName]: choices }));
  } catch {
    // Chat remains usable when browser storage is unavailable.
  }
}

export type AcpInstallJob = {
  state: "running" | "succeeded" | "failed";
  phase: string;
  version: string | null;
  downloaded_bytes: number;
  total_bytes: number | null;
  error: string | null;
};

export function installProgressLabel(job: AcpInstallJob): string {
  if (job.state === "failed") return job.error || "The install failed.";
  if (job.phase === "up_to_date") return `Already up to date${job.version ? ` (${job.version})` : ""}.`;
  if (job.phase === "installed") return `Installed version ${job.version ?? ""}.`.replace(" .", ".");
  if (job.phase === "resolving") return "Checking the ACP Registry…";
  if (job.phase === "extracting") return "Unpacking…";
  if (job.phase === "downloading") {
    const megabytes = (value: number) => Math.round(value / (1024 * 1024));
    return job.total_bytes
      ? `Downloading ${megabytes(job.downloaded_bytes)} of ${megabytes(job.total_bytes)} MB…`
      : `Downloading ${megabytes(job.downloaded_bytes)} MB…`;
  }
  return "Working…";
}
