# Architecture

## Overview
The system is split into four clear layers:
1. UI (React)
2. API (FastAPI)
3. Trading Engine (Stateful, restart-safe)
4. Broker (Zerodha – source of truth)

## Data Ownership
- Broker positions & PnL: Zerodha
- Trade decisions: TradeStateManager
- History & audit: logs/YYYY-MM-DD.log

## Backend Layers

### API Layer
- api_server.py: FastAPI entry point
- api/zerodha_routes.py: Login, callback, status
- api/broker_routes.py: Read-only broker truth

### Auth
- ZerodhaTokenManager
  - Loads/saves daily access token
  - Validates token
  - Provides API-safe login URL

### Trading
- TradeStateManager
  - One instance per CE / PE
  - Controls trade lifecycle
  - Writes audit logs
  - Restart-safe via state files

### Execution
- ZerodhaOrderExecutor
  - Wraps KiteConnect
  - Places orders (LOG or LIVE)

### Logging
- Audit logger: append-only disk logs
- Log bus: pushes logs to UI (non-critical)

## Key Rules
- API must never block
- UI displays, never assumes
- Zerodha is always the truth for positions
