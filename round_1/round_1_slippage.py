import jsonpickle
from datamodel import OrderDepth, TradingState, Order
from typing import List


class Trader:
    POSITION_LIMITS = {
        "INTARIAN_PEPPER_ROOT": 80,
        "ASH_COATED_OSMIUM": 80,
    }

    # Osmium: stable mean-reverting asset
    OSMIUM_FV = 10000
    OSMIUM_SPREAD = 2  # wider than ±1 → captures more edge per fill
    OSMIUM_AGGRESSIVE_EDGE = 2  # only hit asks/bids that are ≥2 away from FV


    # Osmium stop loss: max unrealized loss (current_value - entry_value) before
    # we unwind aggressively. After the position returns to flat, the entry
    # baseline resets and normal market-making resumes on the next tick.
    # Units: ticks × lots. E.g. 200 = ~10 ticks of adverse move on 20 lots.
    # Increase to tolerate wider swings; decrease to cut losses sooner.
    OSMIUM_STOP_LOSS = 999

    # Pepper: timestamps per day = 0..999900
    PEPPER_SLOPE = 0.001  # FV rises 1 per 1000 ticks
    PEPPER_DAY_STEP = 1000  # FV jumps 1000 between days
    PEPPER_BUY_UNTIL = 50_000   # buy aggressively in the first ~5% of the day
    PEPPER_SELL_FROM = 950_000  # sell aggressively in the last ~5% of the day
    PEPPER_BUY_INTERVAL = 5  # add this as a class-level constant

    # Pepper trailing stop: how many ticks below the peak mid (since entry) we
    # tolerate before force-unwinding. After the unwind the peak resets and the
    # strategy resumes normally — it will re-enter during the next buy window.
    # E.g. 50 = tolerate a 50-tick pullback from the highest point since entry.
    # Increase to ride through bigger pullbacks; decrease to protect gains faster.
    PEPPER_TRAILING_STOP = 999

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
                    order_depth, position, limit, state.timestamp, trader_data
                )
            elif product == "ASH_COATED_OSMIUM":
                osm_fv = trader_data.get("osm_ewm", self.OSMIUM_FV)
                orders = self._trade_osmium(
                    order_depth, position, limit, osm_fv, trader_data
                )

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

    def _liquidate(
        self,
        product: str,
        order_depth: OrderDepth,
        position: int,
    ) -> List[Order]:
        """
        Aggressively flatten the current position by hitting the best
        available prices on the opposite side. Used by both stop loss paths.
        Returns sell orders if long, buy orders if short, empty list if flat.
        """
        orders: List[Order] = []
        if position > 0:
            # Long → hit bids to sell
            sell_capacity = position
            for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                if sell_capacity <= 0:
                    break
                qty = min(order_depth.buy_orders[bid_price], sell_capacity)
                orders.append(Order(product, bid_price, -qty))
                sell_capacity -= qty
        elif position < 0:
            # Short → hit asks to buy back
            buy_capacity = abs(position)
            for ask_price in sorted(order_depth.sell_orders.keys()):
                if buy_capacity <= 0:
                    break
                qty = min(abs(order_depth.sell_orders[ask_price]), buy_capacity)
                orders.append(Order(product, ask_price, qty))
                buy_capacity -= qty
        return orders

    # ------------------------------------------------------------------
    # INTARIAN_PEPPER_ROOT — Trend-Ride Strategy with Trailing Stop
    # ------------------------------------------------------------------

    def _trade_pepper(
        self,
        order_depth: OrderDepth,
        position: int,
        limit: int,
        timestamp: int,
        trader_data: dict,
    ) -> List[Order]:
        """
        Trend-riding strategy:
          • Early in day  → buy at best ask only (avoids ask2/ask3 slippage)
          • Mid day       → hold; trailing stop watches for reversal
          • Late in day   → sell aggressively back to 0

        Trailing stop (soft):
          Tracks the highest mid seen since entering a long position in
          traderData["pep_peak_mid"]. If the current mid falls more than
          PEPPER_TRAILING_STOP ticks below that peak, _liquidate() is called
          immediately and the peak is cleared. On the very next tick the
          strategy is back to normal — no flag, no lockout. It will re-enter
          during the buy window or stay flat through mid-day until it does.
        """
        orders: List[Order] = []
        intraday_t = timestamp % 1_000_000  # 0..999999 within each day
        mid = self._calc_mid(order_depth)

        
        # --- Update trailing peak whenever we hold a long position ---
        if position > 0 and mid is not None:
            current_peak = trader_data.get("pep_peak_mid") or mid
            trader_data["pep_peak_mid"] = max(current_peak, mid)

        # --- Trailing stop check (runs every tick while long) ---
        if position > 0 and mid is not None:
            peak = trader_data.get("pep_peak_mid", mid)
            drawdown = peak - mid
            if drawdown >= self.PEPPER_TRAILING_STOP:
                # Stop triggered: unwind aggressively then let strategy resume
                orders = self._liquidate("INTARIAN_PEPPER_ROOT", order_depth, position)
                # Clear peak so it resets cleanly on the next entry
                trader_data["pep_peak_mid"] = None
                return orders
        
        
        # How many ticks to wait between buys. At 100-unit timestamps per tick,
        # PEPPER_BUY_INTERVAL=20 means one buy attempt every 2000 timestamp units.
        # Increase to spread buys further apart; decrease to fill faster.

        if intraday_t <= self.PEPPER_BUY_UNTIL:
            # BUY PHASE: buy (ask1_volume - 1) each tick, leaving one unit on the
            # book to avoid fully depleting the ask level each tick.
            buy_capacity = limit - position
            if buy_capacity > 0 and order_depth.sell_orders:
                best_ask = min(order_depth.sell_orders.keys())
                available = abs(order_depth.sell_orders[best_ask])
                qty = min(max(available - 1, 0), buy_capacity)
                if qty > 0:
                    orders.append(Order("INTARIAN_PEPPER_ROOT", best_ask, qty))

        elif intraday_t >= self.PEPPER_SELL_FROM:
            # SELL PHASE: liquidate the full position at end of day
            orders = self._liquidate("INTARIAN_PEPPER_ROOT", order_depth, position)

        # Mid-day with no stop trigger: hold, no orders.
        return orders

    # ------------------------------------------------------------------
    # ASH_COATED_OSMIUM — EWM Fair-Value Market Making with Loss Stop
    # ------------------------------------------------------------------

    def _trade_osmium(
        self,
        order_depth: OrderDepth,
        position: int,
        limit: int,
        fv: float,
        trader_data: dict,
    ) -> List[Order]:
        """
        Mean-reverting market making around EWM fair value.
        Aggressive: hit any ask < fv-edge or bid > fv+edge.
        Passive: post at fv±spread to collect the spread.

        Loss-based stop (soft):
          On the first tick we hold a non-zero position, traderData["osm_entry_value"]
          is set to position * mid as the fair-value cost basis. Each subsequent tick
          unrealized_pnl = (position * mid) - osm_entry_value is computed. If it
          drops below -OSMIUM_STOP_LOSS, _liquidate() is called and the snapshot
          is cleared. Once flat, the snapshot stays None until the next non-zero
          position, at which point normal market-making has already resumed.
        """
        orders: List[Order] = []
        spread = self.OSMIUM_SPREAD
        edge = self.OSMIUM_AGGRESSIVE_EDGE
        mid = self._calc_mid(order_depth)

        # --- Entry value snapshot ---
        # Track cash flow from fills by comparing position change each tick.
        # When position grows (e.g. -3 → -8), the 5-unit fill happened at the
        # passive quote price we posted last tick (fv ± spread), so we record
        # that cash delta. This gives a correct cost basis regardless of how
        # many fills arrive over time.
        prev_position = trader_data.get("osm_prev_position", 0)
        position_delta = position - prev_position
        trader_data["osm_prev_position"] = position

        if position_delta != 0:
            # Infer fill price: buys fill at our ask (fv + spread),
            # sells fill at our bid (fv - spread)
            fill_price = fv + spread if position_delta > 0 else fv - spread
            cash_delta = -position_delta * fill_price  # negative for buys, positive for sells
            trader_data["osm_cash"] = trader_data.get("osm_cash", 0) + cash_delta

        if position == 0:
            trader_data["osm_cash"] = 0

       # --- Loss stop check ---
        if position != 0 and mid is not None:
            cash = trader_data.get("osm_cash", 0)
            unrealized_pnl = cash + position * mid
            if unrealized_pnl <= -self.OSMIUM_STOP_LOSS:
                trader_data["osm_cash"] = 0
                trader_data["osm_prev_position"] = 0
                return self._liquidate("ASH_COATED_OSMIUM", order_depth, position)
        

        # --- Normal market-making ---
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