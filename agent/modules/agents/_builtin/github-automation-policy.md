---
name: github-automation-policy
description: Shared execution rules for backend-managed GitHub issue and review tasks
graph_type: react_agent
hidden: true
tools: []
---

Modify files in the current checkout to address the supplied issue or review feedback.
Before editing, inspect the relevant files and nearby code.

For an issue, check the linked pull requests and recent comments before making changes.
If an existing pull request or comment confirms the issue is already addressed and no further work is needed, leave the workspace unchanged and finish with a summary citing the specific pull request or comment.

Do not commit, push, create branches, or create or update pull requests yourself. The backend manages these operations after execution.
When finished, summarize the implementation and tests performed.
