# Computer use

Agents see and control the graphical desktop of a workspace machine with two
shared tools, `computer_screenshot` and `computer_action`. They work the same on
every workspace backend that has a desktop; today that is Instabox.

## Layers

| Layer | Location | Responsibility |
| --- | --- | --- |
| Tools | `agent/modules/tools/builtin/computer.py` | Schema, permissions, secrets, screenshots in the workspace, partial-failure reporting |
| Shared computer layer | `agent/modules/workspaces/computer.py` | Action vocabulary and validation, key names, screenshot scaling, human control, per-machine lock, viewer tickets, `xdotool` translation for X11 |
| Backend | e.g. `InstaboxComputer` in `agent/modules/workspaces/instabox_backend.py` | Raw primitives of the `WorkspaceComputer` protocol |
| Dashboard | `agent/delivery/http/dashboard/routes/computers.py`, `WorkspaceDesktop.tsx` | Status, start, take control, hand back and the VNC viewer for any backend |

## Adding a backend

Implement `WorkspaceComputer` and set `computer_loader` on the backend
descriptor. The loader is the only declaration needed: it gives the backend the
`desktop` capability, which makes the dashboard show the Desktop tab and keeps
the computer tools visible to agents working there.

```python
class MyComputer:
    async def status(self, locator) -> dict: ...
    async def start(self, locator, *, width, height) -> dict: ...
    async def screenshot(self, locator) -> ComputerScreenshot: ...
    async def act(self, locator, step: ComputerStep) -> dict: ...
    async def set_human_control(self, locator, active: bool) -> dict: ...
    async def viewer_endpoint(self, locator) -> ViewerEndpoint: ...
    def invalidate(self, locator) -> None: ...
```

- `screenshot` and `act` start the desktop when needed and raise `ComputerError`.
- `act` receives validated steps in screen pixels with canonical key names and
  never `wait`. X11 desktops can reuse `xdotool_input(step)`.
- `set_human_control` mirrors the dashboard lock to the backend's own lock, if
  it has one; otherwise it returns the status.
- `viewer_endpoint` returns an RFB (VNC) WebSocket URL with the headers the
  server uses to authenticate. The browser never sees them.
- The shared layer handles human control, serialization, scaling and tickets.
  Backends must not reimplement them.

`tests/test_computer_tools.py` runs the tools, routes and control lock against
an in-memory `FakeComputer`; use it as the contract test for new backends.

## Tool behavior

- **Several inputs per call.** `computer_action` takes `actions`, a list of up
  to 20 steps: `click`, `double_click`, `triple_click`, `move`, `drag`, `scroll`,
  `type`, `key`, `open_url` and `wait`. Clicks, `move`, `drag` and `scroll`
  accept held `modifiers` (`ctrl`, `alt`, `shift`, `super`); clicks and drags
  accept `button`. The reply shows the screen after the last step unless
  `screenshot: false`.
- **One thing per step.** Every step shares one flat set of fields. Null, `""`
  and `[]` count as absent, and settings an action does not use (`button`,
  `direction`, coordinates, …) are ignored. `text`, `secret`, `keys` and `url`
  on an action that does not use them are refused before anything runs, because
  they mean the model expected two inputs from one step (a click that also
  types).
- **Coordinates.** Screenshots report their size. Screens larger than 1568 px
  on a side or 1.15 megapixels are downscaled for the model, and coordinates
  are mapped back to the screen. Steps outside the last screenshot are refused.
  When the remembered screen is lost (restart, restored machine), the tool
  measures the screen again before sending coordinates.
- **Screenshots in context.** The model is sent the latest 3 to 5 desktop
  screenshots; older ones become a text placeholder, three at a time so prompt
  caches keep matching. The saved conversation and the files in the workspace
  are unchanged.
- **Keys.** Single characters or `enter`, `escape`, `tab`, `backspace`,
  `delete`, `insert`, `space`, arrow keys, `home`, `end`, `pageup`,
  `pagedown`, `capslock`, `printscreen`, `menu`, `f1`–`f24` and the modifiers.
  Common aliases (`Return`, `Esc`, `Control`, `cmd`, `PageDown`) are accepted.
- **Failures.** If a step fails after earlier steps ran, the reply lists the
  steps that ran so the agent does not repeat them. A failed screenshot after
  successful input is reported without hiding the input. `type` waits as long
  as the text takes to type.
- **Secrets.** `type` with `secret: "NAME"` types a project secret on the
  server. Its value never appears in arguments, results or logs.
- **One call per machine per turn.** Parallel computer calls on the same
  machine are refused; calls on different machines may run together.
- **Human control.** While a person controls a desktop from the dashboard,
  agent input and screenshots on it are refused with `human_in_control`.
  Control expires 30 seconds after the last heartbeat.
- **Screenshots in the workspace.** The latest 20 images are kept under
  `.k41-agent/computer/screenshots/` in the main workspace, named by time.
- **Visibility.** The tools are hidden when neither the main workspace nor an
  auxiliary machine the agent can reach has a desktop.

## Permissions

| Action | Resource | Default |
| --- | --- | --- |
| `computer_screenshot` | machine ID | allow |
| `computer_click`, `computer_move`, `computer_drag`, `computer_scroll`, `computer_type`, `computer_key`, … | machine ID | allow |
| `computer_open_url` | the URL | ask |
| `computer_type_secret` | machine ID (each secret name is granted separately) | ask |

`computer_open_url` matches the URL, so a rule can allow trusted sites:
`{"action": "computer_open_url", "resource": "https://github.com/*", "effect": "allow"}`.
"Allow for this conversation" covers the action on that machine regardless of
coordinates or typed text; the approval card still shows them.

## Dashboard API

Routes under `/dashboard-api/computers/{backend}/{locator}` require an admin
session: `GET` (status), `POST start`, `POST takeover`, `POST heartbeat`,
`POST handback` and `POST viewer-ticket`. The viewer connects to the WebSocket
`/dashboard-api/computers/viewer` and sends `{"ticket": "..."}` first; tickets
are single-use and expire after 60 seconds.
