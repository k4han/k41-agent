# Prompt ownership

Agent card bodies own roles, task policies, and response requirements. Tool
descriptions own tool selection and usage rules. Runtime context supplies data
such as workspace paths, available skills, callable agents, and input budgets.

## Tool descriptions and discovery

`write_todos`, `ask_user`, `request_plan_review`, `skill`, and `delegate_task`
carry their usage instructions in their descriptions. The workflow does not
append a second copy of those instructions to the system prompt.

`build_llm_system_prompt` renders the executing card and workspace context. Its
`sections` output also collects discovery catalogs. The system prompt cache
retains those catalogs with the prompt, using the existing agent, skill,
workspace, permission scope, and revision keys.

The assembled prompt cache defaults to 300 seconds, so ordinary user pauses and
tool calls do not trigger repeated database reads and skill discovery. Local
revision changes invalidate entries immediately. External changes, including
writes by other workers, are observed after the fixed TTL; cache hits do not
extend it. Configure `prompt_cache.ttl_seconds` for a shorter refresh interval,
or set it to zero to disable caching.

`bind_tool_catalogs` attaches each catalog to a shallow copy of the corresponding
tool before model binding. Discovery catalogs retain every available name and
its full description as compact single-line entries. Skill XML tags and file
locations are omitted; loading a skill supplies its instructions and resource
paths on demand. These selection summaries still consume input tokens on each
turn, in proportion to the number of available skills and callable agents.
Registry tools are never modified. Empty callable
agent catalogs remove `delegate_task`. The LLM node retains an enabled `skill`
tool even with an empty catalog, so activation never changes its schema. Backend
authorization remains independent of these descriptions.

Context accounting attributes discovery catalogs to skills/subagents and counts
the remaining tool schema separately, so moving a catalog does not double-count
its tokens. Active skill instructions remain in their full `skill` tool responses,
never in the system prompt. Stable activation call IDs link these responses to
checkpointed snapshots. Compaction restores missing call/result pairs verbatim,
outside the generated summary, without retaining unrelated sibling tool outputs.
Channel trimming uses the same restoration before checking the input budget.
Instruction tokens count as skills, rather than ordinary tool output. Usage
metadata records `context_prompt_breakdown` separately for the fixed system and
tool schemas, preventing manual compaction from reserving skill history twice.

## Internal agent cards

The `decision_agent` graph supplies curated workspace data through one automatic
`codebase_context({})` call/result pair per user turn. The unchanged user message
is followed by the pair, which retains the bounded directory tree and selected
file contents in checkpoint history. Later model calls replay that snapshot
without rereading files or appending data to the system prompt. Each new turn
appends its own snapshot. The internal tool schema stays fixed while the decision
run or its tool history exists, including agents with no other tools. A repeated
call returns the current turn's retained snapshot, or an error if none remains.

Reference-only checkpoints are upgraded once when their active turn resumes.
The state marker `decision_context_tool_call_id` prevents reconstruction after
ordinary compaction. Context snapshots use the existing tool display and token
accounting, and follow ordinary tool history compaction rather than skill
instruction restoration. Their file contents remain reference data.

The following internal cards own instructions for existing workflow steps:

| Card | Consumer |
| --- | --- |
| `research-planner` | Research directions in `research_chain` |
| `research-summarizer` | Research report in `research_chain` |
| `context-file-selector` | LLM fallback for workspace file selection |
| `context-file-curator` | LLM fallback for file inclusion |
| `github-automation-policy` | Backend-managed GitHub issue/review requests |

Clone these builtins to override their instructions using the usual agent card
override mechanism. They supply reusable prompts to existing steps, rather than
starting new agent sessions. Model selection, reasoning effort, and context
budgets remain owned by the executing workflow. The research steps also render
the executing agent's instructions, so its preferences apply to both stages.

`render_agent_prompt` resolves configured prompt variables and supplied runtime
values. `context-file-selector` receives `max_candidates` from the workflow.
Structured response schemas stay in Python. Missing or empty internal card
prompts raise an error instead of silently using a hardcoded replacement.

System prompt variables describe the workspace execution environment. Local
workspaces use the coding service's configured shell. Sandbox workspaces read
OS, shell, and user information from the coding worker inside the sandbox;
`working_dir` uses the backend's resolved root, while `workspace` keeps its
friendly label. Runtime lookup failures propagate instead of substituting host
values. The assembled prompt cache remains scoped to the workspace execution.

`host_os` is the OS variable shown in the prompt variable catalog.
`operating_system` remains a reserved runtime alias for existing templates and
is omitted from the catalog to avoid duplicate entries.

GitHub automation policy is shared across issue and review tasks even when a
repository selects a custom executing agent. Manual repository tasks receive
their request and repository context without the backend automation policy.
Issue bodies, comments, linked PRs, and repository instructions remain task data.

## Other templates

- `decisions/instructions.py` owns typed decision question instructions. Request
  data belongs in the question state, and the existing public evaluators remain
  available to integrations.
- `skills/prompts.py` owns resource and execution guidance, including shell
  syntax and runtime dependencies. Package management only supplies runtime data.
- `workspaces/prompt_context.py` owns the shared internal storage layout. Tool
  output pagination and generated image presentation belong to their tools.
- `conversations/prompts.py` owns title/compaction input wrappers and dynamic
  summary budgets. Summarization and title behavior stay in their existing cards.
- `agent_runtime/input_messages.py` owns fallback text for captionless attachments.
