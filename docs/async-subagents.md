# Asynchronous subagents

Dashboard agents with callable `sub_agents` automatically receive `start_async_task`,
`check_async_task`, `list_async_tasks`, `update_async_task`, and `cancel_async_task`.
The existing `delegate_task` still waits for its child. Per-run tool allowlists and
explicit denies also apply to the new tools. ACP and non-dashboard runs do not
expose asynchronous delegation.

Delegation has exactly two levels: a main agent can call a subagent, and every
delegated run is a leaf. A subagent's configured `sub_agents` are ignored for that
run; synchronous and asynchronous delegation tools are both removed. The same
agent retains its configured targets when selected as the main conversation agent.

`start_async_task(task, sub_agent)` commits admission before returning a task ID.
The child has an isolated conversation, inherits execution permissions, workspace,
provider/model overrides and skill restrictions, and uses its own agent card's tools.
It does not receive the parent's message history. The tool call identity deduplicates
admission when LangGraph replays a tool. Follow-up instructions are durable FIFO runs
on the same child thread; they never replace a pending question or approval.

## Execution and recovery

`subagent_tasks`, `subagent_executions`, and `subagent_notifications` store task
state, runnable work, and the parent outbox. `AppContainer.async_subagents` owns the
dispatcher and closes it before streams and database connections. SQLite and
PostgreSQL use the same repository. The initial deployment supports one application
process. No external broker or Agent Server is needed.

Jobs have expiring leases and attempt identities. A process restart reclaims an
expired lease within 30 seconds. Managed graph runs use synchronous checkpoint
durability and an execution marker. Recovery starts a never-admitted input, resumes
an unfinished checkpoint with `None`, preserves interrupts, or finalizes an already
finished checkpoint without another model call. External effects still require each
tool's idempotency guarantees. Shutdown requests a graph drain before bounded
cancellation; shutdown cancellation leaves work recoverable.

Task references and notification cursors live in `BaseState`, outside compacted
messages. Results are delivered through a transactional outbox. An active parent
receives notifications at its next model call; an idle parent receives a durable
wakeup run. Thread admission never replaces an active user run. Pending human
interrupts defer wakeups. Checkpointed cursors deduplicate delivery, including the
gap between committing graph state and acknowledging the outbox. Router wakeups
reuse the originating selected agent.

Task statuses are `pending`, `running`, `waiting`, `completed`, `failed`,
`cancelled`, and `timed_out`. Each execution result has its own notification and
the task reference always includes a monotonic version. A queued follow-up can
leave a task `pending` after an earlier execution finishes.

## Dashboard and HTTP

The dashboard shows task status independently of the launch tool's completion.
Expanded cards reuse the v3 child replay stream and checkpoint history. Session SSE
publishes `subagent_task_updated`; reconnects reload durable snapshots. User input,
tool permissions, and plan reviews resume the child's exact interrupt. The parent
cannot answer on the user's behalf. Existing ACP and browser handoff restrictions
remain in force.

Active tasks are also loaded independently of transcript artifacts. Their controls
remain available after history compaction, pagination, or an interrupted launch.

Authenticated endpoints under `/v2/api/chat/{thread_id}/async-tasks`:

| Method and suffix | Purpose |
| --- | --- |
| `GET /` | Paginated tasks, optional `status`; results are omitted |
| `GET /{task_id}` | Status, result, error, queued updates and pending interrupts |
| `POST /{task_id}/updates` | `{instructions, request_id}`; `request_id` deduplicates retries |
| `POST /{task_id}/cancel` | Cancel the task and descendants |
| `POST /{task_id}/resume` | `{run_id, interrupt_id, payload}` with the existing human resume payload |

Mutations return `202` after persistence. Unknown task ownership returns `404`;
stale interrupts and incompatible task transitions return `409`. Child history
remains available through the existing `/subagents/{delegation_id}` endpoint.

Closing the browser or finishing the parent response leaves children running.
Stopping the parent cancels its task tree and suppresses late wakeups. Deletion
also fences work before removing checkpoints. Conversation forks copy read-only
history without jobs or notification cursors. Rewinding or editing a conversation
with live tasks or undelivered notifications requires stopping that work first.

Configuration defaults are `subagents.max_concurrency: 8`
and `subagents.max_concurrency_per_thread: 4`. The `status=active` list filter
includes pending, running, and waiting tasks.
`subagents.run_timeout_seconds: 0` leaves execution time unlimited; a positive value
limits each active execution attempt. Waiting does not occupy an execution slot.
Schema creation adds the three tables to existing installations; older checkpoints
read the new state channels as empty.
