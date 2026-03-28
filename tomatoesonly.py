import jsonpickle
from datamodel import OrderDepth, TradingState, Order
from typing import List


class Trader:
    POSITION_LIMITS = {
        "TOMATOES": 20,
    }

    BB_WINDOW = 20
    BB_NUM_STD = 2.0
    MAX_TRADE_SIZE = 1

    def run(self, state: TradingState):
        result = {}
        trader_state = jsonpickle.decode(state.traderData) if state.traderData else {}

        for product, order_depth in state.order_depths.items():
            if product not in self.POSITION_LIMITS:
                continue

            orders: List[Order] = []
            position = state.position.get(product, 0)
            limit = self.POSITION_LIMITS[product]

            if product == "TOMATOES":
                orders, trader_state = self._trade_tomatoes(
                    order_depth, position, limit, trader_state
                )

            result[product] = orders

        return result, 0, jsonpickle.encode(trader_state)

    def _trade_tomatoes(self, order_depth, position, limit, trader_state):
        orders = []

        if not order_depth.buy_orders or not order_depth.sell_orders:
            return orders, trader_state

        best_bid = max(order_depth.buy_orders.keys())
        best_ask = min(order_depth.sell_orders.keys())
        bid_vol = order_depth.buy_orders[best_bid]
        ask_vol = abs(order_depth.sell_orders[best_ask])
        mid = (best_bid + best_ask) / 2.0

        prices = trader_state.get("tom_prices", [])
        prices.append(mid)
        if len(prices) > self.BB_WINDOW:
            prices = prices[-self.BB_WINDOW :]
        trader_state["tom_prices"] = prices

        if len(prices) < self.BB_WINDOW:
            return orders, trader_state

        mean = sum(prices) / len(prices)
        variance = sum((p - mean) ** 2 for p in prices) / len(prices)
        std = variance**0.5

        if std == 0:
            return orders, trader_state

        upper = mean + self.BB_NUM_STD * std
        lower = mean - self.BB_NUM_STD * std

        # --- EXITS FIRST ---
        if position > 0 and mid >= mean:
            size = min(position, bid_vol)
            if size > 0:
                orders.append(Order("TOMATOES", best_bid, -size))
                position -= size

        elif position < 0 and mid <= mean:
            size = min(abs(position), ask_vol)
            if size > 0:
                orders.append(Order("TOMATOES", best_ask, size))
                position += size

        # --- ENTRIES ONLY WHEN FLAT (signal on mid, execute on ask/bid) ---
        if position == 0:
            if mid <= lower:
                size = min(self.MAX_TRADE_SIZE, ask_vol)
                if size > 0:
                    orders.append(Order("TOMATOES", best_ask, size))

            elif mid >= upper:
                size = min(self.MAX_TRADE_SIZE, bid_vol)
                if size > 0:
                    orders.append(Order("TOMATOES", best_bid, -size))

        return orders, trader_state
