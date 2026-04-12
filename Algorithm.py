import jsonpickle
from datamodel import OrderDepth, TradingState, Order
from typing import List


class Trader:
    POSITION_LIMITS = {
        "EMERALDS": 20,
        "TOMATOES": 20,
    }

    # ── EMERALDS: fixed fair-value market making ───────────────────────────────
    EMERALD_FV = 10000
    EMERALD_SPREAD = 1

    # ── TOMATOES: inside-spread market making + inventory skew ────────────────
    OFFSET = 3  # half-spread we quote around fair value (ticks)
    SKEW_FACTOR = 0.5  # ticks of quote skew per unit of inventory
    SOFT_LIMIT = 15  # max abs position before we stop adding inventory
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
            limit = self.POSITION_LIMITS[product]

            if product == "EMERALDS":
                orders = self._trade_emeralds(order_depth, position, limit)

            elif product == "TOMATOES":
                orders, trader_state = self._trade_tomatoes(
                    order_depth, position, trader_state
                )

            result[product] = orders

        return result, 0, jsonpickle.encode(trader_state)

    # =========================================================================
    def _trade_emeralds(
        self, order_depth: OrderDepth, position: int, limit: int
    ) -> List[Order]:
        orders: List[Order] = []
        fv = self.EMERALD_FV
        spread = self.EMERALD_SPREAD
        buy_capacity = limit - position
        sell_capacity = limit + position

        # Take profitable asks
        for ask_price in sorted(order_depth.sell_orders.keys()):
            if ask_price < fv and buy_capacity > 0:
                qty = min(-order_depth.sell_orders[ask_price], buy_capacity)
                orders.append(Order("EMERALDS", ask_price, qty))
                buy_capacity -= qty

        # Take profitable bids
        for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
            if bid_price > fv and sell_capacity > 0:
                qty = min(order_depth.buy_orders[bid_price], sell_capacity)
                orders.append(Order("EMERALDS", bid_price, -qty))
                sell_capacity -= qty

        # Passive quotes
        if buy_capacity > 0:
            orders.append(Order("EMERALDS", int(fv - spread), buy_capacity))
        if sell_capacity > 0:
            orders.append(Order("EMERALDS", int(fv + spread), -sell_capacity))

        return orders

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
        skew = position * self.SKEW_FACTOR
        our_bid = round(mid - self.OFFSET - max(0.0, skew))
        our_ask = round(mid + self.OFFSET - min(0.0, skew))

        if our_bid >= our_ask:
            our_bid = round(mid) - 1
            our_ask = round(mid) + 1

        # ── available room on each side ───────────────────────────────────────
        room_long = self.SOFT_LIMIT - position
        room_short = self.SOFT_LIMIT + position

        # ── BB trend filter ───────────────────────────────────────────────────
        trend_suppresses_buy = mid >= upper
        trend_suppresses_sell = mid <= lower

        # ── BUY limit order ───────────────────────────────────────────────────
        if room_long > 0 and not trend_suppresses_buy and our_bid >= best_bid:
            buy_qty = min(room_long, total_ask_vol)
            if buy_qty > 0:
                orders.append(Order("TOMATOES", our_bid, buy_qty))

        # ── SELL limit order ──────────────────────────────────────────────────
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
