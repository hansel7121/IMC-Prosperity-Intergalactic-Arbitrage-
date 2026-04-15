import jsonpickle
from datamodel import OrderDepth, TradingState, Order
from typing import List


class Trader:
    POSITION_LIMITS = {
        "INTARIAN_PEPPER_ROOT": 20,
        "ASH_COATED_OSMIUM": 20,
    }

    # Osmium: stable mean-reverting asset
    OSMIUM_FV = 10000
    OSMIUM_SPREAD = 2  # wider than ±1 → captures more edge per fill
    OSMIUM_AGGRESSIVE_EDGE = 2  # only hit asks/bids that are ≥2 away from FV

    # Pepper: timestamps per day = 0..999900
    PEPPER_SLOPE = 0.001  # FV rises 1 per 1000 ticks
    PEPPER_DAY_STEP = 1000  # FV jumps 1000 between days
    PEPPER_BUY_UNTIL = 50_000  # buy aggressively in the first ~5% of the day
    PEPPER_SELL_FROM = 950_000  # sell aggressively in the last ~5% of the day

    def run(self, state: TradingState):
        result = {}
        trader_data = jsonpickle.decode(state.traderData) if state.traderData else {}

        # --- Osmium: maintain EWM of mid price ---
        osm_depth = state.order_depths.get("ASH_COATED_OSMIUM")
        if osm_depth:
            mid = self._calc_mid(osm_depth)
            if mid is not None:
                prev_ewm = trader_data.get("osm_ewm", mid)
                alpha = 2 / (20 + 1)  # span=20 EWM
                trader_data["osm_ewm"] = alpha * mid + (1 - alpha) * prev_ewm

        for product, order_depth in state.order_depths.items():
            if product not in self.POSITION_LIMITS:
                continue

            orders: List[Order] = []
            position = state.position.get(product, 0)
            limit = self.POSITION_LIMITS[product]

            if product == "INTARIAN_PEPPER_ROOT":
                orders = self._trade_pepper(
                    order_depth, position, limit, state.timestamp
                )
            elif product == "ASH_COATED_OSMIUM":
                osm_fv = trader_data.get("osm_ewm", self.OSMIUM_FV)
                orders = self._trade_osmium(order_depth, position, limit, osm_fv)

            result[product] = orders

        return result, 0, jsonpickle.encode(trader_data)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _calc_mid(self, order_depth: OrderDepth):
        """Best bid/ask midpoint, or None if one side is empty."""
        best_bid = max(order_depth.buy_orders.keys(), default=None)
        best_ask = min(order_depth.sell_orders.keys(), default=None)
        if best_bid and best_ask:
            return (best_bid + best_ask) / 2
        return None

    # ------------------------------------------------------------------
    # INTARIAN_PEPPER_ROOT — Trend-Ride Strategy
    # ------------------------------------------------------------------

    def _pepper_fv(self, timestamp: int) -> float:
        """
        Intraday FV derived from the observed linear trend.
        NOTE: 'day' is not passed in TradingState directly, so we infer
        the intraday component only; the offset cancels out in edge calcs.
        We use a conservative buffer rather than an absolute FV so we
        don't need the day number.
        """
        # We only care about the intraday slope for aggressive pricing;
        # we track time within day via timestamp mod 1_000_000.
        return self.PEPPER_SLOPE * timestamp  # intraday FV increment

    def _trade_pepper(
        self,
        order_depth: OrderDepth,
        position: int,
        limit: int,
        timestamp: int,
    ) -> List[Order]:
        """
        Trend-riding strategy:
          • Early in day  → buy aggressively up to limit
          • Mid day       → hold (no orders needed, price rising in our favour)
          • Late in day   → sell aggressively back to 0
        """
        orders: List[Order] = []
        intraday_t = timestamp % 1_000_000  # 0..999999 within each day

        if intraday_t <= self.PEPPER_BUY_UNTIL:
            # --- BUY PHASE: accumulate as fast as possible ---
            buy_capacity = limit - position
            for ask_price in sorted(order_depth.sell_orders.keys()):
                if buy_capacity <= 0:
                    break
                qty = min(abs(order_depth.sell_orders[ask_price]), buy_capacity)
                orders.append(Order("INTARIAN_PEPPER_ROOT", ask_price, qty))
                buy_capacity -= qty

        elif intraday_t >= self.PEPPER_SELL_FROM:
            # --- SELL PHASE: liquidate the full position ---
            sell_capacity = position  # only sell what we actually hold (>0)
            for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                if sell_capacity <= 0:
                    break
                qty = min(order_depth.buy_orders[bid_price], sell_capacity)
                orders.append(Order("INTARIAN_PEPPER_ROOT", bid_price, -qty))
                sell_capacity -= qty

        # Mid-day: no orders — we're just riding the trend.
        return orders

    # ------------------------------------------------------------------
    # ASH_COATED_OSMIUM — EWM Fair-Value Market Making
    # ------------------------------------------------------------------

    def _trade_osmium(
        self,
        order_depth: OrderDepth,
        position: int,
        limit: int,
        fv: float,
    ) -> List[Order]:
        """
        Mean-reverting market making around EWM fair value.
        Aggressive: hit any ask < fv-edge or bid > fv+edge.
        Passive: post at fv±spread to collect the spread.
        """
        orders: List[Order] = []
        spread = self.OSMIUM_SPREAD
        edge = self.OSMIUM_AGGRESSIVE_EDGE

        buy_capacity = limit - position
        sell_capacity = limit + position

        # 1. Aggressive buys: only when ask is clearly below FV
        for ask_price in sorted(order_depth.sell_orders.keys()):
            if ask_price >= fv - edge:
                break
            if buy_capacity <= 0:
                break
            qty = min(abs(order_depth.sell_orders[ask_price]), buy_capacity)
            orders.append(Order("ASH_COATED_OSMIUM", ask_price, qty))
            buy_capacity -= qty

        # 2. Aggressive sells: only when bid is clearly above FV
        for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
            if bid_price <= fv + edge:
                break
            if sell_capacity <= 0:
                break
            qty = min(order_depth.buy_orders[bid_price], sell_capacity)
            orders.append(Order("ASH_COATED_OSMIUM", bid_price, -qty))
            sell_capacity -= qty

        # 3. Passive market-making: post inside the natural market spread
        if buy_capacity > 0:
            orders.append(Order("ASH_COATED_OSMIUM", int(fv - spread), buy_capacity))
        if sell_capacity > 0:
            orders.append(Order("ASH_COATED_OSMIUM", int(fv + spread), -sell_capacity))

        return orders
