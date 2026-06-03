# Bugfix: Default Agent Tools Not Loading

**Date:** 2026-04-01  
**Commit:** 62abab2  
**Severity:** High (affects Telegram default agent)

## Problem

Telegram truyền `agent_name="default"` nhưng tools không được load từ agent config.

## Root Cause

Trong `agent/modules/agent_runtime/application/runner.py`, có logic sai:

```python
# ❌ BUG: Skip loading tools when agent_name="default"
if agent_name and agent_name != "default":
    catalog = get_catalog_service()
    agent_config = catalog.get_agent(agent_name)
    if agent_config:
        allowed_tool_names = agent_config.tools or None
```

Khi `agent_name="default"`:
- Condition `agent_name != "default"` → `False`
- Skip loading tools từ agent config
- Default agent không có tools!

## Solution

Remove check `!= "default"`:

```python
# ✅ FIX: Load tools for ALL agents including default
if agent_name:
    catalog = get_catalog_service()
    agent_config = catalog.get_agent(agent_name)
    if agent_config:
        allowed_tool_names = agent_config.tools or None
```

## Files Changed

- `agent/modules/agent_runtime/application/runner.py`
  - `run_agent()` function (line 90-98)
  - `run_agent_stream()` function (line 135-142)

## Tests Added

- `tests/test_default_agent_tools.py` - 4 tests verifying default agent tools
- `tests/test_default_agent_bugfix.py` - 2 regression tests

**Result:** 38/38 tests passing ✅

## Impact

### Before Fix
- ❌ Telegram default agent had NO tools
- ❌ Tools from `~/.kaka-agent/agents/default.md` were ignored
- ❌ Any agent named "default" couldn't use tools

### After Fix
- ✅ Telegram default agent has tools: `['list_dir', 'read_file', 'write_file', 'search_files']`
- ✅ Tools from `~/.kaka-agent/agents/default.md` are loaded
- ✅ All agents work correctly regardless of name

## Why This Bug Existed

The original logic assumed "default" was a special hardcoded agent that shouldn't load from config. However, with the new agent system:
- "default" is now a regular agent loaded from MD files
- It should be treated like any other agent
- The special case check was no longer needed

## Verification

```bash
# Run tests
uv run pytest tests/test_default_agent_bugfix.py -v

# Expected output:
# ✅ test_run_agent_loads_tools_for_default_agent PASSED
# ✅ test_telegram_default_agent_gets_tools PASSED
```

## Related Issues

- Original implementation: Commit 474fd12
- This fix ensures consistency with the new agent architecture

## Lessons Learned

1. **Avoid special-casing by name** - Treat all agents uniformly
2. **Test edge cases** - "default" is a common name that should be tested
3. **Regression tests** - Add tests for bugs to prevent recurrence
