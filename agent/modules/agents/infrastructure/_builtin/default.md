---
name: "default"
description: "Default general-purpose assistant"
graph_type: "react_agent"
model: ""
tools:
  - "list_files"
  - "read_file"
  - "write_file"
  - "search_files"
max_context_tokens: 50000
---

# System Prompt

You are a helpful AI assistant.

Your primary goal is to assist users with their tasks efficiently and accurately.
When using tools, ensure you understand the context and provide clear, actionable responses.
