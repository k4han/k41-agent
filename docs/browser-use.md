# Browser automation

K41 runs local Chrome through Stagehand v4. Enable the Browser tools for the desired agent in Settings > Agents > Tools. The default agent's tools are unchanged.

Install dependencies with `uv sync`. Chrome or Chromium must be installed separately. Configure `browser.executable_path` in Settings > Configuration if automatic discovery fails. Browser libraries do not require a second model or a Browserbase account.

## Using the browser

Ask the agent to open a JavaScript website, interact with its forms, or test a web app. The tools are `browser_snapshot`, `browser_run`, `browser_screenshot`, `browser_handoff`, and `browser_close`. Use browser tools sequentially. A run accepts either JavaScript over Stagehand's Playwright-style facade or snapshot-ID actions. Take a new snapshot after navigation or stale IDs. A successful human handoff includes a fresh snapshot in its result.

Open the Browser tab in the workspace to watch the active browser alongside the conversation. The chat header shortcut appears only while that conversation has an active browser session and also opens this tab. The workspace's New tab menu can restore the tab after closing. When you request direct browser interaction, or the agent needs human input such as a login, CAPTCHA, or two-factor authentication, the agent calls `browser_handoff`. The workspace opens and selects Browser automatically while the workflow pauses and displays the tool's reason as instructions. Interact directly with the streamed browser, then choose Return control to agent. The runtime captures a fresh snapshot before the model resumes, and the agent uses it to continue the requested task or ask what to do next. Switching workspace tabs, closing the tab, or disconnecting the viewer does not return control or close the browser session. Cancel browser task ends the handoff and closes that browser.

For example, "Open google.com so I can enter my search and give control back" requires `browser_run` to open Google, followed by `browser_handoff` alone in a separate tool call. Opening the page or replying that it is ready does not enable user input. A request such as "Open Google and search for the weather for me" asks the agent to perform the search itself; handoff is needed only if human input becomes necessary. Browser tool descriptions own these selection and usage rules.

Each conversation has its own persistent login profile by default. Open Login profiles in the Browser toolbar to create and select a named profile and reuse a login across conversations. Close the browser before changing profiles. A named profile can be open in only one conversation at a time. Profiles belong to the authenticated dashboard account, independently of the chat request's user ID.

Browser tools require the authenticated account identity supplied by dashboard/API runs. Channel and scheduler runs do not supply this identity, so browser tools, including human handoff, fail before creating a browser session or ownership binding. Open the conversation in the dashboard to perform browser tasks.

Delegated sub-agents inherit the parent run's authenticated browser owner. They can use browser tools, and their browsers can be inspected and closed through the dashboard API using the sub-thread ID. Human handoff is unavailable inside delegated sub-agents because delegation does not relay workflow interrupts. A sub-agent must report any need for user interaction to its parent agent.

## Configuration and storage

| Setting | Default | Behavior |
| --- | --- | --- |
| `browser.executable_path` | Empty | Discover Chrome automatically; applies to new sessions. |
| `browser.storage_root` | `~/.k41-agent/browser` | Database-scoped profiles and images outside project workspaces; restart after changing. |
| `browser.idle_ttl_seconds` | `600` | Close inactive sessions and limit the lifetime of a pending handoff. Viewing frames does not reset the timer. |
| `browser.operation_timeout_seconds` | `60` | Close a browser when an operation times out. |

Tool permissions use each browser tool's name as the action and `*` as the resource, for example `{action: browser_run, resource: '*', effect: ask}` in an agent's `tool_permissions`. This reviews the complete invocation; it is not a per-domain network policy.

Handoff results include `handoff_status` (`returned`, `cancelled`, `expired`, or `session_lost`) and `snapshot_status` (`captured`, `failed`, or `skipped`). A successful return includes the fresh accessibility snapshot in the handoff result, so a separate model-selected `browser_snapshot` call is unnecessary. Automatic observation checks `browser_snapshot` permissions without starting another permission interrupt inside the settled handoff. If observation is denied or requires approval, control still returns and the result contains `snapshot_error`; the agent must resolve it through an explicit `browser_snapshot` call before acting or describing the page. Snapshot failures are reported without claiming to have observed the page, and snapshot timeouts close the browser. The completed handoff receipt retains its observation for replay without repeating user interaction or snapshot capture.

Completed browser mutations are journaled by tool call ID. Replaying a completed call returns its receipt. If an action loses its response or the process stops during execution, its result is unknown; inspect the page before issuing a new action rather than retrying the same call. Browser process state is separate from LangGraph checkpoints. After a browser is lost during handoff, continue or cancel the expired handoff before starting a new browser task. Pending handoffs are automatically cancelled after `browser.idle_ttl_seconds`; expiry closes any matching browser and retains a cancellation receipt for safe workflow replay. Legacy pending handoffs without an expiry are cancelled when next accessed or swept.

Deleting a conversation closes its browsers and all delegated sub-thread browsers, revokes viewer tickets, and removes bindings, invocation receipts, screenshots, run artifacts, and default login directories. Explicitly named login profiles remain available to other conversations. Run artifacts are stored per conversation, independently of the selected login profile.

Viewer tickets are single-use, expire after 60 seconds, and are issued through authenticated, CSRF-protected requests. The viewer checks its Origin and accepts input only during a handoff, from one controlling connection. Streaming frames and human input are not recorded in chat or tool logs. Explicit agent screenshots are saved as access-controlled artifacts.

The browser runs on the K41 host, including for remote workspaces. `localhost` refers to that host. Use a reachable preview URL for Daytona/Modal applications. This version supports one K41 runtime process; cloud browsers, private sandbox port bridges, and file transfer are outside its scope.

## Validation

Run Python browser and API tests with `uv run pytest tests/test_browser_sessions.py tests/test_browser_dashboard.py`. Set `K41_TEST_BROWSER=1` to include `tests/test_browser_live.py`, which exercises installed Chrome using a local fixture without model calls or external accounts. Run `pnpm dashboard:check`, `pnpm dashboard:build`, and the frontend `test:browser-ui` script to validate dashboard interaction and reconnection.

### Manual tool-selection evaluation

Use an authenticated dashboard conversation with Browser tools enabled. Run each scenario in a fresh conversation and inspect the tool calls as well as the viewer. These scenarios evaluate model tool selection; the automated browser tests do not call a model.

| Request or action | Expected behavior |
| --- | --- |
| "Open google.com so I can enter my search and give control back." | Opens Google with `browser_run`, then calls `browser_handoff` alone. The viewer accepts user input and the agent waits. |
| "Open this form; I will fill it in myself." Provide a reachable form URL. | Opens the form, then calls `browser_handoff` before entering any form values. |
| "Let me select the items on this page." Use an already open page. | Calls `browser_handoff` and waits while the user selects items. |
| "Open Google and search for the weather for me." | Performs the search itself; calls `browser_handoff` only if human input such as CAPTCHA is required. |
| Finish a manual search, then choose Return control to agent. | The handoff result has `handoff_status: returned`, `snapshot_status: captured`, and the current page snapshot. The agent acknowledges the observed state and continues the requested task or asks what to do next, without repeating the instructions to search and return control. |
| Choose Cancel browser task during handoff. | Closes the browser and stops the cancelled browser task. |
| Return control with `browser_snapshot` denied or requiring approval. | Reports `returned` with a failed snapshot and a permission error. The agent resolves observation permissions before acting or describing the page. |
