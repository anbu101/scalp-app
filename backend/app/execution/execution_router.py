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

    def get_gtts(self):
        return self._executor.get_gtts()

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

    def cancel_order(self, order_id: str):
        return self._executor.cancel_order(order_id)

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