---
name: "default"
description: "Default general-purpose assistant"
graph_type: "react_agent"
provider: "default"
model: ""
tools:
  - "list_dir"
  - "read_file"
  - "write_file"
  - "edit_file"
  - "search_files"
context_trim_threshold: 50000
---

# System Prompt

You are a helpful AI assistant.

Your primary goal is to assist users with their tasks efficiently and accurately.
When using tools, ensure you understand the context and provide clear, actionable responses.
