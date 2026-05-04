# Post-Deployment Verification Checklist

## Pre-Deployment

- [ ] All tests passing locally (32/32)
- [ ] Git commits pushed to remote
- [ ] Documentation reviewed
- [ ] No uncommitted changes

## Deployment

- [ ] Service deployed successfully
- [ ] No startup errors in logs
- [ ] All 3 graphs registered (check logs for "[Registry] All graphs ready.")
- [ ] Agents loaded from ~/.kaka-agent/agents/

## Smoke Tests

### 1. Health Check
```bash
curl http://localhost:8000/api/health
# Expected: {"status": "ok", "graphs": ["react_agent", "research_chain", "router"]}
```

### 2. List Graphs
```bash
curl http://localhost:8000/api/graphs
# Expected: {"graphs": ["react_agent", "research_chain", "router"]}
```

### 3. Test Default Agent
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Hello, test message",
    "user_id": "test-user"
  }'
# Expected: Response from default agent
```

### 4. Test Named Agent
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Research about LangGraph",
    "agent_name": "research",
    "user_id": "test-user"
  }'
# Expected: Response from research agent
```

### 5. Test Backend Agent
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Write a Python function to calculate fibonacci",
    "agent_name": "backend",
    "user_id": "test-user"
  }'
# Expected: Response from backend agent with Python code
```

## Verification

### Check Logs
- [ ] No errors during startup
- [ ] "Loaded N agent(s) from..." message present
- [ ] "[Registry] All graphs ready." message present
- [ ] No warnings about missing agents

### Check Agent Loading
```python
# Run in Python console
from agent.modules.agents import get_catalog_service

catalog = get_catalog_service()
agents = catalog.list_agents()
print(f"Total agents: {len(agents)}")
for agent in agents:
    print(f"  - {agent.name}: {agent.description}")

# Expected output:
# Total agents: 5+ (including builtin default)
# - default: Default general-purpose assistant
# - backend: Python/backend engineer assistant
# - research: Research specialist...
# etc.
```

### Check Graph Registration
```python
from agent.modules.workflows import list_registered_workflows

graphs = list_registered_workflows()
print(f"Registered graphs: {graphs}")

# Expected: ['react_agent', 'research_chain', 'router']
```

## Performance Tests

### 1. Response Time
- [ ] Default agent responds < 5s
- [ ] Named agent responds < 5s
- [ ] No memory leaks after 100 requests

### 2. Concurrent Requests
```bash
# Send 10 concurrent requests
for i in {1..10}; do
  curl -X POST http://localhost:8000/api/chat \
    -H "Content-Type: application/json" \
    -d "{\"message\": \"Test $i\", \"user_id\": \"user-$i\"}" &
done
wait
```
- [ ] All requests complete successfully
- [ ] No errors in logs
- [ ] Response times acceptable

## Edge Cases

### 1. Non-existent Agent
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Test",
    "agent_name": "nonexistent",
    "user_id": "test-user"
  }'
# Expected: Falls back to default agent (no error)
```

### 2. Empty Message
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "",
    "agent_name": "default",
    "user_id": "test-user"
  }'
# Expected: Handles gracefully
```

### 3. Missing Agent Name
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Test",
    "user_id": "test-user"
  }'
# Expected: Uses default agent
```

## Rollback Plan

If issues occur:

1. Check logs for errors
2. Verify agent files in ~/.kaka-agent/agents/
3. Test agent loading manually
4. If critical: rollback to previous commit
   ```bash
   git revert 48629e8 474fd12
   git push
   # Redeploy
   ```

## Success Criteria

- [ ] All smoke tests pass
- [ ] No errors in logs
- [ ] All agents load correctly
- [ ] Response times acceptable
- [ ] Edge cases handled gracefully
- [ ] Performance tests pass

## Post-Verification

- [ ] Monitor logs for 1 hour
- [ ] Check error rates in monitoring
- [ ] Verify user feedback
- [ ] Document any issues found

---

**Date:** 2026-04-01  
**Verified by:** _____________  
**Status:** [ ] PASS / [ ] FAIL  
**Notes:**
