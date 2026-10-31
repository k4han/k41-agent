# Testing

The Python suite lives in `tests/` plus the provider-specific checks under `agent/internal/`.

## Quick local loop

Run the focused files you are changing:

```powershell
uv run pytest tests/test_<area>.py -q
```

Skip tests that need external tools or opt-in services:

```powershell
uv run pytest -q -m "not integration and not live"
```

## Full suite

```powershell
uv run pytest -q
```

The default run includes every test collected from `tests/` and `agent/internal/`.
Use `--durations=20` to find slow cases.

## Markers

| Marker | Meaning |
| --- | --- |
| `slow` | Takes more than a couple of seconds; excluded from the quick loop. |
| `integration` | Needs external tools or services (uv, pnpm, node, git, Chrome, sandbox). |
| `live` | Opt-in checks against real local services; gated by environment variables. |

Live checks stay skipped unless explicitly enabled:

- `K41_TEST_BROWSER=1` enables `tests/test_browser_live.py`.
- `K41_TEST_DAYTONA_WORKSPACE` / `K41_TEST_MODAL_WORKSPACE` enable
  `tests/test_skill_package_live.py` when set to a `WorkspaceRef` JSON payload.

## Temp files and SQLite

pytest writes databases and scratch files to the OS temp volume, not into the
repository:

- PowerShell: `%TEMP%\k41-pytest`
- POSIX: `$TMPDIR/k41-pytest`

Keep it on a fast local disk. Fsync on slow or network-backed volumes can make
the suite an order of magnitude slower. Override the location when needed:

```powershell
$env:K41_PYTEST_TEMP = "C:\fast-temp\k41-pytest"
uv run pytest -q
```

Each run keeps the five newest session directories under `sessions/`; older
runs are pruned automatically. A schema-only SQLite template is created once per
session and copied into each `isolated_container` test to avoid rebuilding all
tables every time.

## Parallel execution

`pytest-xdist` is available for CPU-heavy test groups:

```powershell
uv run pytest -q -n auto --dist loadscope
```

Keep browser, live, and sandbox tests out of parallel runs unless you have
isolated resources:

```powershell
uv run pytest -q -n auto --dist loadscope -m "not live and not integration"
```

## Timeouts

`pytest-timeout` fails a test that runs longer than 180 seconds. Live tests
override this to 900 seconds. This prevents a stuck browser or subprocess from
blocking a full run.

## Coverage

`pytest-cov` is installed as a development dependency:

```powershell
uv run pytest -q --cov=agent --cov-report=term-missing
```
