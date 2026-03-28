import jsonpickle
from datamodel import OrderDepth, TradingState, Order
from typing import List


class Trader:
    POSITION_LIMITS = {
        "EMERALDS": 20,
        "TOMATOES": 20,
    }

    # Emeralds: tight market-making spread around fixed fair value
    EMERALD_FV = 10000
    EMERALD_SPREAD = 1

    # Bollinger Band parameters for TOMATOES
    BB_WINDOW = 20
    BB_NUM_STD = 2.0
    MAX_TRADE_SIZE = 20

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
                    order_depth, position, limit, trader_state
                )

            result[product] = orders

        return result, 0, jsonpickle.encode(trader_state)

    # ------------------------------------------------------------------
    # EMERALDS — fixed fair-value market making
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # TOMATOES — Bollinger Band mean-reversion
    #
    # Position-limit safety: the engine cancels ALL orders for a product
    # if worst-case position exceeds the limit IN EITHER DIRECTION.
    # It checks buy side and sell side independently:
    #   real_position + total_buy_qty  <= limit
    #   real_position - total_sell_qty >= -limit
    #
    # To avoid submitting mixed-direction orders that trip this check,
    # we never enter on the same tick as an exit.
    # ------------------------------------------------------------------
    def _trade_tomatoes(
        self,
        order_depth: OrderDepth,
        position: int,
        limit: int,
        trader_state: dict,
    ):
        orders: List[Order] = []

        # ---- compute mid price ----
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return orders, trader_state

        best_bid = max(order_depth.buy_orders.keys())
        best_ask = min(order_depth.sell_orders.keys())
        bid_vol = order_depth.buy_orders[best_bid]
        ask_vol = -order_depth.sell_orders[best_ask]
        mid = (best_bid + best_ask) / 2.0

        # ---- update rolling mid-price history ----
        prices: list = trader_state.get("tom_prices", [])
        prices.append(mid)
        if len(prices) > self.BB_WINDOW:
            prices = prices[-self.BB_WINDOW :]
        trader_state["tom_prices"] = prices

        # ---- need a full window before trading ----
        if len(prices) < self.BB_WINDOW:
            return orders, trader_state

        # ---- compute Bollinger Bands (population std, ddof=0) ----
        mean = sum(prices) / len(prices)
        variance = sum((p - mean) ** 2 for p in prices) / len(prices)
        std = variance**0.5
        upper = mean + self.BB_NUM_STD * std
        lower = mean - self.BB_NUM_STD * std

        # ---- exits first (mirrors backtest order) ----
        if best_bid >= mean and position > 0:
            # Exit long: sell at bid
            max_sell = limit + position
            size = min(position, bid_vol, max_sell)
            if size > 0:
                orders.append(Order("TOMATOES", best_bid, -size))
            # Don't enter on the same tick as an exit
            return orders, trader_state

        if best_ask <= mean and position < 0:
            # Exit short: buy at ask
            max_buy = limit - position
            size = min(abs(position), ask_vol, max_buy)
            if size > 0:
                orders.append(Order("TOMATOES", best_ask, size))
            # Don't enter on the same tick as an exit
            return orders, trader_state

        # ---- entries only if flat (no exit happened above) ----
        if position == 0:
            if best_ask <= lower:
                # Buy signal — price at lower band
                size = min(self.MAX_TRADE_SIZE, ask_vol, limit)
                if size > 0:
                    orders.append(Order("TOMATOES", best_ask, size))

            elif best_bid >= upper:
                # Short signal — price at upper band
                size = min(self.MAX_TRADE_SIZE, bid_vol, limit)
                if size > 0:
                    orders.append(Order("TOMATOES", best_bid, -size))

        return orders, trader_state
