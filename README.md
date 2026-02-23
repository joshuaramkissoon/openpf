# MyPF

AI-powered portfolio operations platform built on Claude, with live trading via Trading 212.

<!-- TODO: add screenshot or demo GIF here -->
<!-- ![MyPF Dashboard](docs/assets/screenshot.png) -->

## What it does

- **AI portfolio copilot** -- Chat with Archie, an agent powered by Claude that can analyze your positions, run technical screens, place orders, and manage scheduled tasks.
- **Live broker integration** -- Connects to Trading 212 (Invest + Stocks ISA) for real-time positions, order placement, and trade history.
- **Quantitative toolkit** -- Built-in technical indicators (RSI, MACD, Bollinger Bands, ATR), risk metrics (Sharpe, Sortino, VaR, max drawdown), and strategy backtesting.
- **Leveraged trading automation** -- Signal scanning, configurable risk rails, and policy-driven execution for leveraged products.
- **Persistent agent memory** -- Archie remembers your preferences, goals, constraints, and past decisions across sessions.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    React + Vite UI                       │
│  Portfolio · Research · Execution · Leveraged · Chat     │
└──────────────────────┬──────────────────────────────────┘
                       │ REST + WebSocket
┌──────────────────────▼──────────────────────────────────┐
│                  FastAPI Backend                         │
│  Routes · Services · SQLite · APScheduler               │
├─────────────┬───────────────┬───────────────────────────┤
│ Claude SDK  │  MCP Servers  │  Worker Process           │
│ Chat + Agent│  ┌──────────┐ │  Scheduled tasks          │
│  Runtime    │  │Trading212│ │  Telegram polling         │
│             │  │MarketData│ │                           │
│             │  │Scheduler │ │                           │
│             │  └──────────┘ │                           │
└─────────────┴───────────────┴───────────────────────────┘
```

## Quick start

### Prerequisites

- Python 3.11+
- Node.js 18+
- [Anthropic API key](https://console.anthropic.com/)
- [Trading 212](https://www.trading212.com/) API key (optional -- needed for broker features)

### 1. Backend

```bash
cd backend
cp .env.example .env        # fill in your API keys
python3 -m venv ../.venv
../.venv/bin/pip install -r requirements.txt
../.venv/bin/uvicorn app.main:app --reload --port 8000
```

In a second terminal, start the worker (scheduled tasks + Telegram):

```bash
cd backend
../.venv/bin/python worker.py
```

API docs: http://localhost:8000/docs

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Dashboard: http://localhost:5173

### Docker

```bash
# Hot-reload mode (default)
make up

# Stable mode (no live reload)
make up MODE=stable

# Logs / restart / stop
make logs
make restart
make down
```

### Key environment variables

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API access |
| `CLAUDE_MODEL` | Model for Archie (e.g. `claude-sonnet-4-20250514`) |
| `T212_API_KEY_INVEST` | Trading 212 Invest account key |
| `T212_API_KEY_STOCKS_ISA` | Trading 212 ISA account key |
| `T212_BASE_ENV` | `demo` or `live` |
| `CLAUDE_MEMORY_STRATEGY` | `self_managed`, `distill`, or `off` |

See `backend/.env.example` for the full list.

## Tech stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18, TypeScript, Vite, Recharts, Lightweight Charts |
| Backend | FastAPI, SQLAlchemy, SQLite, APScheduler |
| AI runtime | Claude Agent SDK, MCP (Model Context Protocol) |
| Market data | yfinance (via MCP server) |
| Broker | Trading 212 API (via MCP server) |

## Project structure

```
backend/
  app/
    api/            # FastAPI route handlers
    services/       # Core logic (chat runtime, agent, leveraged, memory)
    quant/          # Technical indicators and risk metrics
    models/         # SQLAlchemy models
    core/           # Config, database setup
  mcp_servers/      # MCP servers (Trading 212, market data, scheduler)
  worker.py         # Background worker process

frontend/
  src/
    components/     # React UI (chat, portfolio, charts, leveraged desk)
    api/            # API client layer
    types/          # TypeScript types

.claude/
  runtime/          # Agent working directory (memory, artifacts, skills)
```

## Testing

```bash
# Compile check
python3 -m compileall backend/app backend/tests

# Run tests (with venv activated)
pytest backend/tests -q
```

## Contributing

Contributions are welcome. Please open an issue to discuss your idea before submitting a PR.

1. Fork the repo
2. Create a feature branch
3. Make your changes
4. Open a pull request

## Disclaimer

This software is for research and personal tooling only. It is not financial advice. You are responsible for all trading decisions, strategy validation, regulatory obligations, and risk management.

## License

MIT
