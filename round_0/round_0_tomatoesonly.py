import jsonpickle
from datamodel import OrderDepth, TradingState, Order
from typing import List


class Trader:
    """
    TOMATOES market maker — posts limit orders inside the spread and
    skews quotes by current inventory to stay flat.

    Why not pure Bollinger entry/exit?
    Round 1 data shows the BB half-width (~2.3 ticks) is smaller than the
    half-spread (~6.5 ticks), so best_ask never touches the lower band.
    The market is mean-reverting (lag-1 autocorr = -0.44), which is a
    market-making signal, not a directional one.

    Strategy:
      - Always quote bid at (mid - OFFSET) and ask at (mid + OFFSET)
      - Skew quotes by inventory: long pos -> lower both prices to sell faster,
        short pos -> raise both prices to buy faster
      - Hard cap on position size (SOFT_LIMIT) so we never hit the Prosperity wall
      - Bollinger Bands used only as a macro trend filter: stop quoting the
        side that would push us further into a trending move
    """

    POSITION_LIMITS = {"TOMATOES": 20}

    # ── market-making params ──────────────────────────────────────────────────
    OFFSET = 3  # half-spread we quote around fair value (ticks)
    SKEW_FACTOR = 0.5  # ticks of quote skew per unit of inventory
    SOFT_LIMIT = 15  # max abs position before we stop adding inventory

    # ── Bollinger params (trend filter only, not entry signal) ────────────────
    BB_WINDOW = 20
    BB_NUM_STD = 2.0

    # =========================================================================
    def run(self, state: TradingState):
        result = {}
        trader_state = jsonpickle.decode(state.traderData) if state.traderData else {}

        for product, order_depth in state.order_depths.items():
            if product not in self.POSITION_LIMITS:
                continue

            orders: List[Order] = []
            position = state.position.get(product, 0)

            if product == "TOMATOES":
                orders, trader_state = self._trade_tomatoes(
                    order_depth, position, trader_state
                )

            result[product] = orders

        return result, 0, jsonpickle.encode(trader_state)

    # =========================================================================
    def _trade_tomatoes(
        self,
        order_depth: OrderDepth,
        position: int,
        trader_state: dict,
    ):
        orders: List[Order] = []

        if not order_depth.buy_orders or not order_depth.sell_orders:
            return orders, trader_state

        # ── market snapshot ───────────────────────────────────────────────────
        best_bid = max(order_depth.buy_orders.keys())
        best_ask = min(order_depth.sell_orders.keys())
        mid = (best_bid + best_ask) / 2.0

        total_bid_vol = sum(order_depth.buy_orders.values())
        total_ask_vol = sum(abs(v) for v in order_depth.sell_orders.values())

        # ── rolling price history for BB trend filter ─────────────────────────
        prices: list = trader_state.get("tom_prices", [])
        prices.append(mid)
        if len(prices) > self.BB_WINDOW:
            prices = prices[-self.BB_WINDOW :]
        trader_state["tom_prices"] = prices

        # compute BB (fall back to wide bands if window not full yet)
        if len(prices) >= self.BB_WINDOW:
            mean = sum(prices) / len(prices)
            variance = sum((p - mean) ** 2 for p in prices) / len(prices)
            std = variance**0.5
            upper = mean + self.BB_NUM_STD * std
            lower = mean - self.BB_NUM_STD * std
        else:
            upper = mid + 9999
            lower = mid - 9999

        # ── quote prices with inventory skew ──────────────────────────────────
        # positive position -> shift both quotes DOWN to encourage selling
        # negative position -> shift both quotes UP to encourage buying
        skew = position * self.SKEW_FACTOR

        our_bid = round(mid - self.OFFSET - max(0.0, skew))
        our_ask = round(mid + self.OFFSET - min(0.0, skew))

        # sanity: never let quotes cross
        if our_bid >= our_ask:
            our_bid = round(mid) - 1
            our_ask = round(mid) + 1

        # ── available room on each side ───────────────────────────────────────
        room_long = self.SOFT_LIMIT - position  # units willing to buy
        room_short = self.SOFT_LIMIT + position  # units willing to sell

        # ── BB trend filter ───────────────────────────────────────────────────
        # Mid above upper band -> suppress buys (trending up, don't fight it)
        # Mid below lower band -> suppress sells (trending down, don't fight it)
        trend_suppresses_buy = mid >= upper
        trend_suppresses_sell = mid <= lower

        # ── BUY limit order ───────────────────────────────────────────────────
        # Only post if competitive (our_bid >= best_bid = top of book)
        if room_long > 0 and not trend_suppresses_buy and our_bid >= best_bid:
            buy_qty = min(room_long, total_ask_vol)
            if buy_qty > 0:
                orders.append(Order("TOMATOES", our_bid, buy_qty))

        # ── SELL limit order ──────────────────────────────────────────────────
        # Only post if competitive (our_ask <= best_ask = top of book)
        if room_short > 0 and not trend_suppresses_sell and our_ask <= best_ask:
            sell_qty = min(room_short, total_bid_vol)
            if sell_qty > 0:
                orders.append(Order("TOMATOES", our_ask, -sell_qty))

        # ── emergency flatten at hard Prosperity limit ────────────────────────
        hard_limit = self.POSITION_LIMITS["TOMATOES"]
        if position >= hard_limit:
            orders.append(Order("TOMATOES", best_bid, -position))
        elif position <= -hard_limit:
            orders.append(Order("TOMATOES", best_ask, abs(position)))

        return orders, trader_state
