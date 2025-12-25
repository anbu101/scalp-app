
# Scalp App

A controlled, auditable intraday options trading system.

## Core Principles
- Zerodha is the source of truth
- Bot decisions are fully auditable
- API never blocks
- Safe by default

## Project Structure
(see backend/app)

## Zerodha Authentication
UI driven login using /zerodha/login-url and /zerodha/callback

## Broker APIs
GET /broker/positions

## Audit Logs
logs/YYYY-MM-DD.log

## Daily Summary
python3 -m tools.generate_daily_summary
