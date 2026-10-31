---
description: Activate reusable skill packages and understand project and shared skill sources.
---

# Skills

A skill packages reusable instructions in `SKILL.md`, with optional scripts,
templates, images, and other resources. Load one when a conversation needs that
particular workflow.

## Activate a skill in chat

Type `/` in the chat input to search available skills. Choose a suggestion with
the mouse, Enter, or Tab, then add the task you want performed.

```text
/skill example-skill Summarize the notes in this workspace.
```

You can also use an explicit load command:

```text
/skill load example-skill
```

Loading the instructions does not put every supporting resource into the model's
context. The agent reads resources as the task needs them.

## Refresh or unload

A conversation normally keeps its loaded skill version. If the source changed,
refresh it explicitly:

```text
/skill refresh example-skill
```

To release its active instructions:

```text
/skill unload example-skill
```

## Where skills come from

Discovery includes project `.agent/skills` and `.agents/skills`, managed user
skills under `~/.k41-agent/skills`, user `.agents/skills`, and any configured
additional roots.

Project skills take precedence over shared skills. The dashboard shows each
package's source, disabled state, shadowing, and validation diagnostics.

## Manage a package

Open Skills settings to create, inspect, enable, disable, or import packages.
You can also inspect project and shared packages from a project's **Skills**
tab; that project view is read-only.

Package imports support a host directory, a ZIP upload, or an HTTPS Git source.
Preview the package before importing it. A Git import records a particular
revision; importing a repository is not the same as continuously tracking its
latest changes.

## Tools still belong to the agent

Skill instructions cannot grant additional tool rights. If a skill needs a
particular capability, configure it on the selected [agent](./agents).

For the full package format and lifecycle, see the repository's
[skill package reference](https://github.com/k4han/k41-agent/blob/main/docs/skills.md).
