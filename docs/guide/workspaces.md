---
description: Organize projects, choose conversation workspaces, and inspect files in the K41 dashboard.
---

# Projects and files

A workspace gives your conversation a place to work. A project adds a persistent
catalog entry so that workspace is easy to return to.

## Choose a conversation workspace

Use the workspace selector in chat to select the context for the task. A local
workspace runs on the K41 host. Optional Daytona, Modal and
[Instabox](https://github.com/k4han/k41-agent/blob/main/docs/instabox.md) backends provide remote execution when configured.

New project conversations use local execution. Moving an existing conversation
to another execution environment is an explicit session change.

**No workspace** means the conversation is not linked to a project. It still
has a private internal folder for files and tools, retained with the conversation
and cleaned up when the conversation is deleted. Older temporary workspaces
appear under the same name without moving their files.

Scheduled tasks default to **No workspace**, including tasks scheduled from a
project conversation. Select a project explicitly in the scheduler form or when
requesting a schedule to use its files. Every scheduled or manual run starts a
separate conversation; project runs share project files, not conversation history.
Open each run from the scheduler's run history. Scheduler conversations do not
appear in the sidebar, and results are no longer copied into a shared user chat.

## Organize your projects

Open **Projects**, or **Manage** beside Projects in the sidebar. You can add,
search, rename, pin, archive, remove, and restore catalog entries.

- **New chat** checks workspace availability before opening a conversation.
- **View history** opens the existing workspace conversation filter.
- **Rename** changes the display name, not the workspace identity.
- **Archive** hides the project from the active sidebar.
- **Remove** changes its catalog status; it does not delete files, conversation
  history, or sandboxes.

Use the corresponding status filter to restore archived or removed projects.

## Inspect workspace files

Open the workspace tree in chat, then choose a file to view it in a tab. Depending
on the format, K41 shows source code, a formatted document, an image, media
controls, or a download option.

See [file previews](./file-previews) for supported formats and preview limits.

## Add project instructions

Open a project and select its **AGENTS.md** tab to inspect or edit the root
instruction document. A new document is written only when you save it.

Use instructions for conventions that apply throughout the project, such as its
package manager, test commands, and source-language requirements.

## Inspect project skills

The project's **Skills** tab shows project and shared packages, their sources,
and any disabled or invalid states. Package resources are read-only in this
view. Use Skills settings for package management.

See [skills](./skills) to activate a workflow from a conversation.

## Keep local data persistent

Project files have a durable identity independent of an optional cloud sandbox's
lifetime. Keep the application database and the configured project storage
directory on persistent disks.

For the detailed storage contract, see the repository's
[project workspace reference](https://github.com/k4han/k41-agent/blob/main/docs/project-workspaces.md).
