# Tool permission inventory

All workflow calls run sequentially in separate LangGraph task checkpoints after whole-batch authorization.
Completed calls are reused on resume. Unknown tools default to dangerous and use the same isolated execution path.
Capabilities never imply safety; contradictory safe/mutating registrations are rejected.

| Tool | Risk | Permission preparation | Business interrupt |
|---|---|---|---|
| ask_user | safe | Shared descriptor authorization | True |
| bash | mutating | CodingService.prepare_batch | False |
| browser_close | mutating | prepare_builtin + execution recheck | False |
| browser_handoff | mutating | prepare_builtin + execution recheck | True |
| browser_run | mutating | prepare_builtin + execution recheck | False |
| browser_screenshot | safe | prepare_builtin + execution recheck | False |
| browser_snapshot | safe | prepare_builtin + execution recheck | False |
| calendar_check_availability | safe | Shared descriptor authorization | False |
| calendar_create_event | mutating | Shared descriptor authorization | False |
| calendar_delete_event | mutating | Shared descriptor authorization | False |
| calendar_get_agenda | safe | Shared descriptor authorization | False |
| calendar_get_event | safe | Shared descriptor authorization | False |
| calendar_list_calendars | safe | Shared descriptor authorization | False |
| calendar_list_events | safe | Shared descriptor authorization | False |
| calendar_update_event | mutating | Shared descriptor authorization | False |
| computer_action | mutating | prepare_builtin + execution recheck | False |
| computer_screenshot | safe | prepare_builtin + execution recheck | False |
| copy_files | mutating | prepare_builtin + execution recheck | False |
| delegate_task | mutating | Shared descriptor authorization | False |
| delete_scheduled_task | mutating | Shared descriptor authorization | False |
| edit | mutating | CodingService.prepare_batch | False |
| generate_image | mutating | Shared descriptor authorization | False |
| get_current_time | safe | Shared descriptor authorization | False |
| github_get_pull_request | safe | Shared descriptor authorization | False |
| github_get_pull_request_diff | safe | Shared descriptor authorization | False |
| github_list_issue_pull_requests | safe | Shared descriptor authorization | False |
| glob | safe | CodingService.prepare_batch | False |
| grep | safe | CodingService.prepare_batch | False |
| html_preview | mutating | Shared descriptor authorization | False |
| instabox_machine_create | dangerous | prepare_builtin + execution recheck | False |
| instabox_machine_delete | dangerous | prepare_builtin + execution recheck | False |
| instabox_machine_fork | dangerous | prepare_builtin + execution recheck | False |
| instabox_machine_get | safe | prepare_builtin + execution recheck | False |
| instabox_machine_list | safe | prepare_builtin + execution recheck | False |
| instabox_machine_resume | mutating | prepare_builtin + execution recheck | False |
| instabox_machine_start | mutating | prepare_builtin + execution recheck | False |
| instabox_machine_stop | mutating | prepare_builtin + execution recheck | False |
| instabox_machine_suspend | mutating | prepare_builtin + execution recheck | False |
| instabox_port_close | mutating | prepare_builtin + execution recheck | False |
| instabox_port_list | safe | prepare_builtin + execution recheck | False |
| instabox_port_publish | mutating | prepare_builtin + execution recheck | False |
| instabox_port_rotate_token | mutating | prepare_builtin + execution recheck | False |
| instabox_port_set_access | mutating | prepare_builtin + execution recheck | False |
| instabox_snapshot_create | mutating | prepare_builtin + execution recheck | False |
| instabox_snapshot_delete | dangerous | prepare_builtin + execution recheck | False |
| instabox_snapshot_get | safe | prepare_builtin + execution recheck | False |
| instabox_snapshot_list | safe | prepare_builtin + execution recheck | False |
| instabox_snapshot_restore | dangerous | prepare_builtin + execution recheck | False |
| list_dir | safe | CodingService.prepare_batch | False |
| list_scheduled_tasks | safe | Shared descriptor authorization | False |
| read | safe | CodingService.prepare_batch | False |
| read_process_output | safe | CodingService.prepare_batch | False |
| request_plan_review | safe | Shared descriptor authorization | True |
| schedule_task | mutating | Shared descriptor authorization | False |
| skill | mutating | Shared descriptor authorization | False |
| stop_process | mutating | CodingService.prepare_batch | False |
| web_fetch | safe | Shared descriptor authorization | False |
| web_search | safe | Shared descriptor authorization | False |
| write | mutating | CodingService.prepare_batch | False |
| write_process_input | mutating | CodingService.prepare_batch | False |
| write_todos | mutating | Shared descriptor authorization | False |

Detailed actions are classified by ACTION_RISK; shell uses classify_command. MCP readOnlyHint=true is safe, otherwise dangerous.
Computer open_url/type_secret and public Instabox ports add dangerous operations. Snapshot restore always confirms outside full_access.
Browser handoff has its own persisted invocation state as well as the per-call task checkpoint.
