# backend/app/execution/execution_router.py

from app.execution.base_executor import BaseOrderExecutor


class ExecutionRouter(BaseOrderExecutor):
    """
    Broker-agnostic execution router.
    Currently wraps Zerodha executor.

    ADDITIVE CHANGES (SCALP_V3 enablement — back-compatible):
      - place_gtt_oco() : now forwards optional last_price + direction params.
            Defaults (last_price=None, direction="LONG") are EXACTLY what the
            underlying executor already used, so existing BB/HA/SCALP_V1 callers
            (which pass only symbol/qty/sl_price/tp_price) are byte-for-byte
            unchanged. SCALP_V3 uses direction="LONG" + tp_price=None for an
            SL-only GTT, and passes a fresh last_price to avoid stale-LTP
            rejections.
      - NEW forwards (purely additive — nothing called these through the router
        before, so no existing path changes):
          * place_market_sell  [V3-REQUIRED] exit the long hedge
          * cancel_gtt          [V3-REQUIRED] cancel hedge SL-GTT before selling
          * get_order_fill      [V3-REQUIRED] two-phase fill confirmation
          * resolve_ltp         [V3-REQUIRED] exit-price resolution (REST primary)
          * place_sell_entry    [completeness] SCALP_V1 short entry
          * place_buy_exit      [completeness] SCALP_V1 short exit
    """

    def __init__(self, underlying_executor: BaseOrderExecutor):
        self._executor = underlying_executor

    # --------------------------------------------------
    # BUY
    # --------------------------------------------------

    def place_buy(self, symbol, token, qty):
        return self._executor.place_buy(symbol, token, qty)

    # ── ACC2_PST ── LIMIT BUY forward (PST resting TP). Pure passthrough,
    # same shape as the other forwards above.
    def place_limit_buy(self, symbol, qty, price, product="MIS", tag=""):
        return self._executor.place_limit_buy(symbol, qty, price,
                                              product=product, tag=tag)

    def get_last_avg_price(self, order_id):
        return self._executor.get_last_avg_price(order_id)

    # --------------------------------------------------
    # GTT
    # --------------------------------------------------

    def place_gtt_oco(self, symbol, qty, sl_price, tp_price,
                      last_price=None, direction="LONG"):
        # BACK-COMPAT: existing callers pass 4 args; last_price/direction then
        # take the defaults the underlying executor already applied, so behaviour
        # is identical for BB / HA / SCALP_V1. SCALP_V3 passes direction="LONG"
        # with tp_price=None (SL-only) and a fresh last_price.
        return self._executor.place_gtt_oco(
            symbol=symbol,
            qty=qty,
            sl_price=sl_price,
            tp_price=tp_price,
            last_price=last_price,
            direction=direction,
        )

    def cancel_gtt(self, gtt_id):
        # [V3-REQUIRED] cancel the hedge SL-only GTT before a signal-driven sell.
        return self._executor.cancel_gtt(gtt_id)
    
    def cancel_gtt_verified(self, gtt_id, retries: int = 2) -> bool:
        # Forward the verify-after-cancel when the underlying executor
        # provides it. If it doesn't (older executor build), degrade to the
        # plain cancel and return True — "True" here means "not confirmed
        # still-armed", matching the old behaviour and avoiding a false
        # GTT_ORPHAN alert when we simply couldn't verify.
        fn = getattr(self._executor, "cancel_gtt_verified", None)
        if callable(fn):
            return fn(gtt_id, retries=retries)
        try:
            self._executor.cancel_gtt(gtt_id)
        except Exception:
            pass
        return True

    def get_gtts(self):
        return self._executor.get_gtts()

    # ── GTT_RACE_STRICT_20260814 BEGIN ── forward the broker-truth reads
    # (2026-08-14 TMA/IC double-exit incidents). None = "could not read /
    # not supported" in both protocols, so degrading to None when the
    # underlying executor lacks the method keeps caller semantics exact.
    def get_gtt_status(self, gtt_id):
        fn = getattr(self._executor, "get_gtt_status", None)
        return fn(gtt_id) if callable(fn) else None

    def get_open_positions_or_none(self):
        fn = getattr(self._executor, "get_open_positions_or_none", None)
        return fn() if callable(fn) else None
    # ── GTT_RACE_STRICT_20260814 END ──

    # --------------------------------------------------
    # SL (legacy safety)
    # --------------------------------------------------

    def place_sl(self, symbol, qty, sl_price):
        return self._executor.place_sl(symbol, qty, sl_price)

    # --------------------------------------------------
    # EXIT
    # --------------------------------------------------

    def place_exit(self, symbol, qty, reason):
        return self._executor.place_exit(
            symbol=symbol,
            qty=qty,
            reason=reason,
        )

    def place_market_sell(self, symbol, qty):
        # [V3-REQUIRED] exit a LONG hedge (sell the bought option). Reuses the
        # same protected-limit sell the BB/HA EOD square-off path uses.
        return self._executor.place_market_sell(symbol=symbol, qty=qty)

    # --------------------------------------------------
    # SHORT side (completeness — SCALP_V1 short entry/exit)
    # --------------------------------------------------

    def place_sell_entry(self, symbol, token, qty):
        # [completeness] not used by SCALP_V3 (V3 is option-buying).
        return self._executor.place_sell_entry(symbol=symbol, token=token, qty=qty)

    def place_buy_exit(self, symbol, qty, reason):
        # [completeness] not used by SCALP_V3.
        return self._executor.place_buy_exit(symbol=symbol, qty=qty, reason=reason)

    # --------------------------------------------------
    # POSITIONS / ORDERS
    # --------------------------------------------------

    def get_open_positions(self):
        return self._executor.get_open_positions()

    def get_order_fill(self, order_id):
        # [V3-REQUIRED] single-call status/avg_price/filled_qty for two-phase
        # fill confirmation (distinguishes pending / COMPLETE / dead).
        return self._executor.get_order_fill(order_id)

    # ── TSG_ROUTER_CONTRACT_20260825 BEGIN ── (2026-08-25 TSG L4 orphan
    # incident: TSG_ENTRY_REPEG added fresh_{sell,buy}_entry_limit /
    # modify_order / cancel_order(symbol=) to ZerodhaOrderExecutor only.
    # Live runs wrap the executor in this router, which forwards explicitly
    # — no __getattr__ — so the first L4 re-peg raised AttributeError, the
    # working order was never cancelled, and it filled 10s after the day
    # closed: an untracked naked long. Forwards below use the same
    # degrade-to-None contract as GTT_RACE_STRICT_20260814: None means
    # "not supported / no fresh quote / MODIFY failed", which every caller
    # already treats as "keep the current limit".)

    def cancel_order(self, order_id: str, symbol: str = ""):
        # symbol is log-cosmetics only (TSG_ENTRY_REPEG D4). Forward it when
        # the underlying executor accepts it; degrade to the positional call
        # so no pre-existing executor or call site can break.
        try:
            return self._executor.cancel_order(order_id, symbol=symbol)
        except TypeError:
            return self._executor.cancel_order(order_id)

    def modify_order(self, order_id: str, price: float, symbol: str = ""):
        # Re-peg MODIFY (TSG_ENTRY_REPEG D1). None = "MODIFY failed" —
        # the caller keeps waiting on the old price.
        fn = getattr(self._executor, "modify_order", None)
        if not callable(fn):
            return None
        return fn(order_id, price=price, symbol=symbol)

    def fresh_sell_entry_limit(self, symbol: str):
        # Re-peg price for a working SELL entry. None = "no fresh quote".
        fn = getattr(self._executor, "fresh_sell_entry_limit", None)
        return fn(symbol) if callable(fn) else None

    def fresh_buy_entry_limit(self, symbol: str):
        # Re-peg price for a working BUY entry. None = "no fresh quote".
        fn = getattr(self._executor, "fresh_buy_entry_limit", None)
        return fn(symbol) if callable(fn) else None
    # ── TSG_ROUTER_CONTRACT_20260825 END ──

    def get_orders(self):
        return self._executor.get_orders()

    # --------------------------------------------------
    # SYMBOL / PRICE RESOLUTION
    # --------------------------------------------------

    def resolve_symbol(self, symbol: str) -> str:
        return self._executor.resolve_symbol(symbol)

    def resolve_ltp(self, symbol: str):
        # [V3-REQUIRED] REST-primary LTP (LTPStore fallback) for exit pricing.
        # Public passthrough to the executor's internal _resolve_ltp so callers
        # never reach a private attribute.
        return self._executor._resolve_ltp(symbol)