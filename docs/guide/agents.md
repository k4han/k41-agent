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

Manually selected tools use an explicit allow-list. An empty manual tool list
does not enable all tools. A skill's instructions do not grant additional tool
permissions.

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
