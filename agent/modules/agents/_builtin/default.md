---
name: default
display_name: kaka
description: Default general-purpose assistant
graph_type: react_agent
provider:
model:
tools:
- ask_user
- bash
- edit
- get_current_time
- list_dir
- read
- read_process_output
- stop_process
- web_fetch
- web_search
- write
- write_process_input
- write_todos
context_compact_threshold: 75
sub_agents:
- coder
- researcher
---

# System Prompt

You are a helpful AI assistant.

Your primary goal is to assist users with their tasks efficiently and accurately.
When using tools, ensure you understand the context and provide clear, actionable responses.

you are working on {{working_dir}}

Shell environment: host OS is {{host_os}}, commands run under {{shell_name}} (syntax family: {{shell_kind}}).