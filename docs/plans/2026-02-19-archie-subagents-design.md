# Archie Subagents — Phase 1 Design

**Date**: 2026-02-19
**Scope**: Core subagent infrastructure for Archie (chat + scheduler runtimes)
**Phase 2**: archie-builds (see `2026-02-19-archie-builds-spec.md`)

---

## Overview

Add subagent delegation support to Archie using the Claude Agent SDK's `agents` parameter. Archie offloads focused subtasks to specialised sub-agents, keeping his main context tight. Full nested observability in the chat UI — subagent cards expand to show each tool call the subagent made, following the existing `ToolCallsSummary` pattern.

---

## Agent Roster

### `researcher`
- **Model**: `sonnet`
- **Purpose**: Financial research, news, company analysis, documentation, market intelligence
- **Tools**: `WebSearch`, `WebFetch`, `Read`, `Glob`, `Grep`, `Write` (artifacts only), all `_MARKET_MCP_TOOLS`
- **Write scope**: `.claude/runtime/artifacts/` only (enforced in prompt)
- **Description** (shown to Archie):
  > Financial research specialist. Delegate here: web research on markets, companies, macro events, financial news, documentation. Provide specific questions and relevant portfolio context. Returns structured research findings and writes artifacts when useful.

### `quant`
- **Model**: `sonnet` (needs statistical reasoning + code writing)
- **Purpose**: Quantitative analysis — write and execute Python scripts, technical indicators, risk metrics, portfolio optimisation, statistical modelling
- **Tools**: `Read`, `Write`, `Edit`, `Glob`, `Grep`, `Bash`, all `_MARKET_MCP_TOOLS`
- **Description**:
  > Quantitative analysis specialist. Delegate here: technical analysis, writing and running Python scripts for data analysis, risk calculations, statistical modelling, portfolio metrics, indicator computation. Provide: instruments, date ranges, and what analysis is needed.

### `execution`
- **Model**: `haiku` (fast, well-defined task)
- **Purpose**: Trade execution — handle multiple orders of different types, return structured typed results
- **Tools**: Narrow T212 subset only (see below) — no file access, no web access
- **T212 tools allowed**:
  ```
  mcp__trading212__get_account_summary
  mcp__trading212__get_positions
  mcp__trading212__get_pending_orders
  mcp__trading212__search_instruments
  mcp__trading212__place_market_order
  mcp__trading212__place_limit_order
  mcp__trading212__place_stop_order
  mcp__trading212__place_stop_limit_order
  mcp__trading212__cancel_order
  mcp__trading212__get_order_history
  ```
- **Skill file**: `.claude/runtime/skills/execution-rules/SKILL.md` — Archie reads this before delegating; contains evolving trade execution rules (risk limits, sizing constraints, order type guidance)
- **Description**:
  > Trade execution specialist. Delegate ALL order placement and cancellation here. Always pass: current account balance, relevant positions, and exact order instructions (symbol, type, qty, limit price if applicable). Returns a typed list of trade results — do not attempt T212 tool calls yourself.

#### Execution Agent Structured Output Schema

The execution agent always returns a final JSON block in this shape. Archie parses it rather than reasoning over raw API output:

```json
{
  "trades": [
    {
      "symbol": "AAPL",
      "type": "market_buy",
      "quantity": 10,
      "status": "filled",
      "fill_price": 182.50,
      "order_id": "uuid-...",
      "error": null
    }
  ],
  "errors": [],
  "commentary": "Executed 2 of 3 orders. Stop-limit on NVDA rejected: insufficient margin."
}
```

---

## Backend Changes

### 1. `claude_sdk_config.py` — Add `build_subagents()`

Add a new public function `build_subagents() -> dict[str, AgentDefinition]` that constructs and returns all three agent definitions. The function imports `AgentDefinition` from `claude_agent_sdk`.

MCP tool lists (`_T212_MCP_TOOLS`, `_MARKET_MCP_TOOLS`) are currently defined in `claude_chat_runtime.py`. Move them to `claude_sdk_config.py` so `build_subagents()` can reference them without a circular import. `claude_chat_runtime.py` and `claude_agent_runtime.py` import the lists from there.

Execution-agent-specific T212 subset defined as `_EXECUTION_T212_TOOLS` in `claude_sdk_config.py`.

### 2. `claude_chat_runtime.py` — Wire subagents + extend streaming

**Options changes** (`_build_options`):
- Add `"Task"` to `allowed_tools`
- Add `agents=build_subagents()` to `ClaudeAgentOptions`
- Add tool labels for Task tool: `"Task": "Delegating to subagent"`

**Stream processing changes** (`stream_reply`):

Track subagent state across messages:
```python
active_subagents: dict[str, str] = {}   # tool_use_id → subagent_type
subagent_nested: dict[str, list] = {}   # tool_use_id → list of nested call dicts
```

Detection logic (integrated into the existing message loop):

1. **Task tool_use seen** (name == "Task"):
   - Extract `subagent_type` from `block.input`
   - Record in `active_subagents[tool_use_id] = subagent_type`
   - Init `subagent_nested[tool_use_id] = []`
   - Emit `subagent_start` status phase

2. **Message with `parent_tool_use_id` set**:
   - Look up `parent_tool_use_id` in `active_subagents`
   - Extract tool events normally via `_extract_tool_events` / `_extract_tool_results`
   - Emit `subagent_tool_start` / `subagent_tool_result` phases (carrying `subagent_id`)
   - Append to `subagent_nested[parent_tool_use_id]`

3. **Task tool_result seen** (matching a tracked tool_use_id):
   - Emit `subagent_result` status phase
   - Append final subagent entry to `collected_tool_calls` with `nested_calls`

**Updated `collected_tool_calls` format** (stored in DB per message):
```python
# Subagent entry
{
    "phase": "subagent_start",
    "message": "Delegating to researcher",
    "subagent_type": "researcher",
    "subagent_id": "tu_abc123",
    "nested_calls": [
        {"phase": "tool_start", "message": "Searching the web", "tool_input": {"query": "..."}},
        {"phase": "tool_result", "message": "Searching the web — done"}
    ]
}
# Regular tool entry (unchanged)
{"phase": "tool_start", "message": "Fetching positions", "tool_input": {...}}
```

**WebSocket status event shapes** (new phases added to existing event format):
```json
{"type": "status", "phase": "subagent_start",      "message": "Delegating to researcher", "tool_input": {"subagent_id": "tu_...", "subagent_type": "researcher"}}
{"type": "status", "phase": "subagent_tool_start",  "message": "Searching the web",        "tool_input": {"subagent_id": "tu_...", "query": "AAPL earnings"}}
{"type": "status", "phase": "subagent_tool_result", "message": "Searching the web — done", "tool_input": {"subagent_id": "tu_..."}}
{"type": "status", "phase": "subagent_result",      "message": "researcher — done",        "tool_input": {"subagent_id": "tu_..."}}
```

### 3. `claude_agent_runtime.py` — Wire subagents

Same as chat runtime:
- Add `"Task"` to `allowed_tools`
- Add `agents=build_subagents()` to `ClaudeAgentOptions`

No live streaming UI for the agent runtime — but `collected_tool_calls` tracking should still be added so scheduled task logs can show subagent activity.

### 4. `task_scheduler_service.py`

The `_run_claude_prompt()` function builds its own `ClaudeAgentOptions`. Apply the same changes: add Task tool, add agents. Import `build_subagents` from `claude_sdk_config`.

---

## Frontend Changes

### `AgentChatPanel.tsx` — Nested subagent observability

**New status phases to handle in the stream client / status trail:**
- `subagent_start`: Open a subagent card; `tool_input.subagent_type` gives the agent name
- `subagent_tool_start` / `subagent_tool_result`: Append to the active subagent card's nested list; grouped by `tool_input.subagent_id`
- `subagent_result`: Finalise the subagent card (mark complete, record duration)

**`StreamSegment` type extension:**
```typescript
type StreamSegment =
  | { kind: 'thinking' | 'tool_start' | 'tool_result' | 'text'; id: string; text: string; toolInput?: Record<string, unknown> }
  | { kind: 'subagent'; id: string; subagentType: string; status: 'running' | 'done' | 'error'; nestedSegments: StreamSegment[] }
```

**`SubagentCard` component** (new, or inline in `ToolCallsSummary`):
- Collapsible card within the tool timeline
- Header: agent-type icon + name (e.g. "researcher") + status badge + elapsed time
- Body (expanded): nested tool timeline using the same item style as existing tool calls
- Visual treatment: left border accent in a distinct colour (e.g. indigo), slight indent

**History rendering**: When loading past messages, parse `tool_calls` JSON from DB. Subagent entries (those with `subagent_type` field) render as `SubagentCard` with `nested_calls` populating the body. Backwards compatible — old tool_call entries without `subagent_type` render as before.

---

## Archie's Instructions — CLAUDE.md Addition

Add a `## Subagent Delegation` section to `.claude/runtime/.claude/CLAUDE.md`:

```markdown
## Subagent Delegation

Use the Task tool to delegate to subagents. You are responsible for passing sufficient context — subagents have no memory of your conversation.

| Agent | When to use |
|---|---|
| `researcher` | Web research, news, company analysis, documentation. Give: specific questions + portfolio context. |
| `quant` | Technical analysis, Python scripts, risk metrics, indicator computation. Give: instruments, date ranges, what to compute. |
| `execution` | ALL order placement and cancellation. Give: account balance, current positions, exact order details (symbol, type, qty, price). Never call T212 trading tools yourself. |

**Before delegating to `execution`**: read `.claude/runtime/skills/execution-rules/SKILL.md` for current trade execution rules.

**Context is your responsibility.** Subagents cannot ask follow-up questions mid-task. Front-load everything they need.
```

---

## Execution Rules Skill File

Create `.claude/runtime/skills/execution-rules/SKILL.md` with initial content:

```markdown
# Execution Rules

Rules Archie must follow before delegating to the execution agent.

## Pre-Execution Checks
- Confirm the position does not already exist at the target size (check positions first)
- Confirm sufficient cash balance for buy orders
- For limit/stop orders, confirm the price level makes sense given current market price

## Order Sizing
- Default to fractional shares for market orders if the notional value exceeds £500 and full shares would over-allocate
- Never size a single order above 10% of total portfolio value without explicit user confirmation

## Risk
- Do not place orders in instruments flagged as illiquid (bid-ask spread > 2%)
- Stop-loss orders should accompany any leveraged position

## Output
Always return structured JSON with the execution schema. Never return unstructured text.
```

---

## Security

- `build_security_hooks()` is passed to the parent `ClaudeAgentOptions`. Verify at runtime that hooks are propagated to subagents by the SDK. If not automatically propagated, the `agents` parameter may need a `hooks` field — check SDK release notes and patch accordingly.
- Subagents do not have `Task` in their tools (SDK enforces: "Subagents cannot spawn their own subagents").
- Execution agent: narrowest possible T212 tool subset — no CSV export, no dividend/transaction history.
- Quant agent: `Bash` access for script execution — security hooks block `rm` and `.env` access.

---

## File Change Summary

| File | Change |
|---|---|
| `backend/app/services/claude_sdk_config.py` | Add `build_subagents()`, move MCP tool lists here, add `_EXECUTION_T212_TOOLS` |
| `backend/app/services/claude_chat_runtime.py` | Import tool lists from sdk_config, add Task tool + agents, extend stream processing for subagent events |
| `backend/app/services/claude_agent_runtime.py` | Add Task tool + agents to options |
| `backend/app/services/task_scheduler_service.py` | Add Task tool + agents to `_run_claude_prompt` options |
| `frontend/src/components/AgentChatPanel.tsx` | Handle new subagent status phases, add SubagentCard rendering |
| `.claude/runtime/.claude/CLAUDE.md` | Add Subagent Delegation section |
| `.claude/runtime/skills/execution-rules/SKILL.md` | New — execution rules skill file |
