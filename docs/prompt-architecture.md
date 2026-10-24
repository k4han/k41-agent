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

`bind_tool_catalogs` attaches each catalog to a shallow copy of the corresponding
tool before model binding. Registry tools are never modified. Empty callable
agent catalogs remove `delegate_task`; an empty skill catalog removes `skill`
unless an active skill still needs unload or refresh operations. Backend
authorization remains independent of these descriptions.

Context accounting attributes discovery catalogs to skills/subagents and counts
the remaining tool schema separately, so moving a catalog does not double-count
its tokens. Active skill instructions continue to be supplied in system context.

## Internal agent cards

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
