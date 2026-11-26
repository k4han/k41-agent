---
description: Configure agent instructions, model choices, tools, and reusable task behavior.
---

# Agents

An agent combines instructions, a workflow, model settings, and a set of
capabilities. Use different agents when different kinds of work need different
instructions or tools.

## Configure an agent

Open **Settings > Agents** and select an agent, or create a new one.

| Tab | What to configure |
| --- | --- |
| **General Config** | Name, description, workflow, model settings, and context options. |
| **Capabilities & Tools** | Available tools, MCP installations, and callable sub-agents. |
| **System Prompt** | The instructions that define how the agent approaches its work. |

Select the desired agent when starting or working in a conversation.

## Give instructions a clear purpose

Describe the task, expected output, and conventions the agent should follow.
For example:

```text
You help maintain the documentation in the selected workspace.
Read the relevant source before describing behavior.
Keep examples short and use the project's existing terminology.
When changing a guide, check its internal links.
```

Use the project's root `AGENTS.md` for project-wide conventions and
[skills](./skills) for reusable task instructions and resources.

## Choose tools deliberately

The agent's capabilities determine what work it can perform. Enable the file,
coding, connection, or browser tools needed for the intended task.

By default, manually selected tools use an explicit allow-list. An empty manual tool list
does not enable all tools. A skill's instructions do not grant additional tool
permissions.

To enable on-demand tools, select `search_tools` or `execute_tool` in the agent's
tool picker. The picker toggles the pair together. Keep frequently used tools
checked to preload them. Other eligible tools can be discovered and used with
their saved settings. MCP tools must belong to a server enabled for that agent.
Tools that ask for user input, request plan review, update the todo list, or
hand off the browser must be selected on the card. If a run supplies a custom
tool allow-list, the agent can only use tools allowed by both the card and the run.

For example, a card with `tools: [web_search, search_tools, execute_tool]`
can call `web_search` directly. Calling `search_tools("web_fetch")` returns
the usage instructions and input schema in a tool message. The agent can then
call `execute_tool("web_fetch", {"url": "https://example.org"})`. A known tool
name also works without a prior search. The target's permissions and the run's
tool restrictions apply to every call.

For website tasks, follow [browser setup](./browser). For external tools, add a
[connection](./connections) and make it available to the intended agent.

## Select models and delegation

An agent can follow the configured default model or use its own provider and
model settings. It can also select a [model pool](./model-pools).

Callable sub-agents are explicitly configured. Delegation is unavailable when
no sub-agents are configured; defining another agent does not automatically
make it callable by every agent.

## Customize built-in behavior

Some built-in agents are internal helpers rather than selectable chat agents.
Use the dashboard's clone and configuration workflow to customize built-in
cards instead of assuming every internal helper appears in the chat selector.
