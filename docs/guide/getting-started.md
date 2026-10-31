---
description: Connect a model provider, choose a workspace, and start your first K41 Agent conversation.
---

# Your first conversation

With [K41 installed](./installation), you only need a configured model provider
to get started. Channels, MCP servers, and browser tools can wait until you need
them.

## 1. Start K41

```sh
k41
```

Open [the local dashboard](http://127.0.0.1:4141) and sign in.
If this is a fresh installation, follow the
[first-login instructions](./installation#start-the-dashboard).

## 2. Connect your model

1. Open **Settings > Providers**.
2. Add your supported provider and its credentials.
3. Set a default model and the models you want to enable.
4. Save the provider settings and select your default provider or model as needed.

You bring your own model access. Any provider usage is billed by that provider.
See [model providers](./providers) for the available setup concepts.

## 3. Choose a workspace

Open **Chat** and use the workspace selector to choose the project for this
conversation. For a project-specific task, select the workspace containing the
relevant files.

K41 can read or change workspace files when the selected agent has the necessary
tools and permissions. See [projects and files](./workspaces) to understand the
workspace options.

## 4. Bring a first question

For an existing project, try:

> Read the README and explain how this project is organized. Suggest a good
> starting point for a new contributor.

Or work with notes:

> Summarize the Markdown notes in this workspace and save a short overview of the
> main themes.

Follow the conversation and tool activity in the dashboard. If a tool needs your
approval or additional input, review the request there.

## Make the setup your own

- Choose a different [agent](./agents) for a specialized task.
- Activate a [skill](./skills) when you need a reusable workflow.
- Add [connections](./connections) to give the agent access to another tool.
- Enable [browser automation](./browser) for work that involves a website.
- Explore [scheduled tasks](./automation) for repeatable work.
