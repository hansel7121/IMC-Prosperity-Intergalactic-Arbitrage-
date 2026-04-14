import jsonpickle
from datamodel import OrderDepth, TradingState, Order
from typing import List


class Trader:
    # Update position limits for your requested products
    POSITION_LIMITS = {
        "INTARIAN_PEPPER_ROOT": 20,
        "ASH_COATED_OSMIUM": 20,
    }

    # Fixed Fair Value for Osmium (Treating it like your Emeralds)
    OSMIUM_FV = 10000
    OSMIUM_SPREAD = 1

    def run(self, state: TradingState):
        result = {}
        # Pepper Root is Long Only, no state needed.
        # Osmium is Fixed FV, no state needed (unless you want dynamic mean).
        trader_state = {}

        for product, order_depth in state.order_depths.items():
            if product not in self.POSITION_LIMITS:
                continue

            orders: List[Order] = []
            position = state.position.get(product, 0)
            limit = self.POSITION_LIMITS[product]

            if product == "INTARIAN_PEPPER_ROOT":
                orders = self._trade_pepper_roots(order_depth, position, limit)

            elif product == "ASH_COATED_OSMIUM":
                # COPYING THE EMERALD STRATEGY LOGIC HERE
                orders = self._trade_osmium_fixed_fv(order_depth, position, limit)

            result[product] = orders

        return result, 0, jsonpickle.encode(trader_state)

    def _trade_pepper_roots(
        self, order_depth: OrderDepth, position: int, limit: int
    ) -> List[Order]:
        """Simple Long-Only Accumulator"""
        orders: List[Order] = []
        if position < limit:
            buy_qty = limit - position
            # Sort asks lowest to highest
            for price in sorted(order_depth.sell_orders.keys()):
                vol = abs(order_depth.sell_orders[price])
                take = min(vol, buy_qty)
                orders.append(Order("INTARIAN_PEPPER_ROOT", price, take))
                buy_qty -= take
                if buy_qty <= 0:
                    break
        return orders

    def _trade_osmium_fixed_fv(
        self, order_depth: OrderDepth, position: int, limit: int
    ) -> List[Order]:
        """Copied from your Emerald logic: Fixed Fair-Value Market Making"""
        orders: List[Order] = []
        fv = self.OSMIUM_FV
        spread = self.OSMIUM_SPREAD

        buy_capacity = limit - position
        sell_capacity = limit + position

        # 1. Aggressive: Take profitable asks (price < FV)
        for ask_price in sorted(order_depth.sell_orders.keys()):
            if ask_price < fv and buy_capacity > 0:
                qty = min(abs(order_depth.sell_orders[ask_price]), buy_capacity)
                orders.append(Order("ASH_COATED_OSMIUM", ask_price, qty))
                buy_capacity -= qty

        # 2. Aggressive: Take profitable bids (price > FV)
        for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
            if bid_price > fv and sell_capacity > 0:
                qty = min(order_depth.buy_orders[bid_price], sell_capacity)
                orders.append(Order("ASH_COATED_OSMIUM", bid_price, -qty))
                sell_capacity -= qty

        # 3. Passive: Post quotes around Fair Value
        # This captures the spread when the market is stable
        if buy_capacity > 0:
            orders.append(Order("ASH_COATED_OSMIUM", int(fv - spread), buy_capacity))
        if sell_capacity > 0:
            orders.append(Order("ASH_COATED_OSMIUM", int(fv + spread), -sell_capacity))

        return orders
