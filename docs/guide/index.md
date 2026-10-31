---
title: Welcome
description: Get to know K41 Agent, install it on your machine, and find the guide for your next task.
pageClass: docs-overview
---

<p class="docs-eyebrow">THE K41 AGENT HANDBOOK</p>

# Your workspace, explained.

<p class="docs-lead">A little guidance for your next big idea. Get your local agent running, connect your tools, and make yourself at home.</p>

<div class="docs-cards">
  <a class="docs-card" href="./installation.html"><small>01 / GET SET UP</small><strong>A home on your machine ↗</strong><span>Install K41 Agent on Windows, macOS, or Linux with one command.</span></a>
  <a class="docs-card" href="./getting-started.html"><small>02 / FIND YOUR FLOW</small><strong>Your first conversation ↗</strong><span>Connect a model, choose your workspace, and bring your first question.</span></a>
  <a class="docs-card" href="./connections.html"><small>03 / MAKE CONNECTIONS</small><strong>Your tools, together ↗</strong><span>Explore MCP servers, GitHub, Telegram, and Discord.</span></a>
  <a class="docs-card" href="./cli.html"><small>04 / KEEP IT HANDY</small><strong>The everyday commands ↗</strong><span>Start, stop, update, and look after your local runtime.</span></a>
</div>

## What is K41 Agent?

K41 Agent is an open-source AI workspace assistant with a local runtime and a web
dashboard. It can chat, work with files in a selected workspace, run background
tasks, and connect to optional services.

You choose the model provider, the workspace, and the tools available to each
agent. The software is MIT licensed; model providers and optional external
services may charge separately.

## How the pieces fit together

| Piece | What it does |
| --- | --- |
| **Runtime** | Runs on your machine and coordinates conversations, tools, and tasks. |
| **Dashboard** | Your browser interface for chat, project files, settings, and activity. |
| **Provider** | Connects the runtime to an LLM using your configured credentials. |
| **Workspace** | The files and project context selected for a conversation. |
| **Agent** | Instructions, model choices, and tool settings for a type of work. |
| **Connections** | Optional MCP servers, repositories, and channel integrations. |

## What local-first means

The runtime, configuration, and runtime data live on your machine. Requests to
a configured model provider or an external integration send the content needed
for those requests to that service. Running K41 locally does not mean every model
runs offline.

Start with [installation](./installation), or jump to
[your first conversation](./getting-started) if K41 is already installed.
