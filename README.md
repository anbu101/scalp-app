
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

## 📥 Installation

See [INSTALLATION.md](INSTALLATION.md) for detailed installation instructions.

**Quick Start:**
1. Download from [latest release](https://github.com/anbu101/scalp-app/releases/latest)
2. Install the DMG or extract TAR.GZ
3. Right-click → Open (first time only)

For troubleshooting and detailed steps, refer to the [Installation Guide](INSTALLATION.md).
