# Development Rules & Invariants

## Executor router contract (2026-10-26)

Strategies receive an `ExecutionRouter`, not the broker executor. The router forwards explicitly, with no `__getattr__`. Any new executor method a strategy calls must get a forward in `backend/app/execution/execution_router.py`. `backend/app/execution/test_execution_router_contract.py` fails the build otherwise; run it before every release. History: TSG 2026-08-25; TMA_V2 2026-10-26 (see `INCIDENT_2026-10-26_TMA_EXIT.md`).

## Live exit orders are never abandoned (2026-10-26)

Every live exit order is driven to a confirmed outcome with `app/execution/exit_order_driver.drive_exit_order`: re-peg, cancel on timeout, confirm. A leg that cannot be closed is retried, never booked at a model price. Hedges are sold only after the short is confirmed flat.
