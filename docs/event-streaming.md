# Event streaming

Chat execution uses LangGraph `astream_events(version="v3")`. The graph is driven
once; the HTTP event log supports multiple subscribers and reconnects. HTTP
and background SSE subscribers use a single v3 envelope. CLI and channel views
consume the runtime's normalized application updates.

## HTTP API

Authenticated endpoints return `application/x-ndjson`, with one JSON event per line:

| Endpoint | Purpose |
| --- | --- |
| `POST /v2/api/chat/events` | Start or resume a run using the existing chat request |
| `POST /v2/api/chat/events/edit` | Re-run from an edited message |
| `POST /v2/api/chat/events/reconnect` | Replay an existing run |
| `GET /v2/api/chat/{thread_id}/subagents/{delegation_id}` | Load a delegated run's saved transcript |

Events have `type: "event"`, `method`, `params.namespace`, `params.timestamp`,
`params.data`, `seq`, and `event_id`. Order by `seq`; timestamps are informational.
`event_id` is `<run_id>:<seq>`. Responses expose `X-Run-ID`, `X-Thread-ID`, and
`X-Stream-Version: v3` headers.

Native channels include `messages`, `tools`, `values`, and `lifecycle`. Message
payloads are `[content_block_event, metadata]` pairs. Python message and tool
objects are serialized before transport. Tools can publish progress using
`ToolRuntime.emit_output_delta(value)`.

The `custom:k41` channel carries application events, including thread creation,
titles, context usage, human input requests, and the normalized UI projection.
Application updates have `projection: "ui"` and a `type` identifying the update.
The dashboard uses this projection for text/reasoning to preserve inline thinking
tags and provider-specific normalization. It uses native events for invocation
boundaries, live tool arguments, execution status, output deltas, and delegation
scopes. Display text and tool results from these normalized application updates; native
message events provide invocation identity and live tool arguments.

Reconnect with `{"thread_id": "..."}` to replay from the beginning. To continue
an existing transcript, supply both `run_id` and `after_seq`; only events with
greater sequence numbers are returned. A replaced run returns `409 run_replaced`.
An expired session returns an empty stream; reload checkpoint history in that
case. Recent events stay in memory with a conservative 1 MiB budget. Older events
are serialized and archived to a temporary file in batches; live subscribers read
recent events directly from memory. Oversized events are archived immediately.
Replay remains available for 30 seconds after completion. Disconnecting a
subscriber does not cancel execution.
The existing session-stop endpoint cancels the run, including its delegated work.

The `/v1/api/chat/events` endpoints and their `/api` aliases have been removed.
Background SSE streams publish v3 envelopes under the `agent` event name.
There is no alternate wire format or fallback to graph `astream`.

Idle v2 HTTP streams send an empty NDJSON line every 15 seconds to keep proxy
connections active. Clients must ignore empty lines. These transport heartbeats
do not receive sequence numbers and are not stored in the replay log. HTTP
disconnects close the subscription while the background run remains available
for reconnect. The v1 plain-text stream closes its agent iterator on disconnect;
it does not insert heartbeats into the assistant's text. Dashboard SSE streams
already send named heartbeat events every 15 seconds.

## Delegated history

`delegate_task` forwards isolated child events into the parent's v3 stream, with
a namespace segment `<agent_name>:<delegation_id>`. Child lifecycle events include
the originating `tool_call_id`. Workspace, provider selection, permissions, and
thread isolation continue to follow the delegation policy.

Checkpoint metadata records `k41_parent_thread_id`, `k41_parent_tool_call_id`,
`k41_parent_message_id`, `k41_delegation_id`, and `k41_agent_name`. Completed tool
messages expose a `subagent` artifact containing the child checkpoint reference.
Incomplete calls can recover that reference from checkpoint metadata. The
dashboard loads saved child transcripts when their collapsed details are opened.

Conversation forks copy referenced child snapshots under the new parent thread
and rewrite their references. Deleting the original conversation therefore does
not delete the fork's child history. Older conversations without delegation
metadata continue to display their saved summary results. No database schema
migration or permanent token-event archive is required.
