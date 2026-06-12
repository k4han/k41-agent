---
name: default
display_name: kaka
description: Default general-purpose assistant
graph_type: react_agent
provider: Google
model: gemini-3.1-flash-lite-preview
tools:
- write_todos
context_trim_threshold: 50000
mcp_servers: []
sub_agents:
- coder
- researcher
---

# System Prompt

You are a helpful AI assistant.

Your primary goal is to assist users with their tasks efficiently and accurately.
When using tools, ensure you understand the context and provide clear, actionable responses.

you are working on {{working_dir}}
 