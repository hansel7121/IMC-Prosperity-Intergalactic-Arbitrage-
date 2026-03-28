import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# ── data ──────────────────────────────────────────────────────────────────────
df = pd.read_csv(
    "/Users/hanselchen/Desktop/IMC Prosperity/prices_round_0_combined.csv",
    sep=";",
)
tom = (
    df[df["product"] == "TOMATOES"]
    .sort_values(["day", "timestamp"])
    .reset_index(drop=True)
)
tom["global_timestamp"] = (tom["day"] - tom["day"].min()) * 1_000_000 + tom["timestamp"]

mids = tom["mid_price"].values
timestamps = tom["global_timestamp"].values


# build full book: list of (price, vol) for bids/asks at each tick
def book_side(row, prefix, levels=3):
    """Return list of (price, volume) tuples, best first."""
    pairs = []
    for lvl in range(1, levels + 1):
        p = row.get(f"{prefix}_price_{lvl}")
        v = row.get(f"{prefix}_volume_{lvl}")
        if pd.notna(p) and pd.notna(v) and v > 0:
            pairs.append((float(p), int(v)))
    return pairs


bid_book = [book_side(row, "bid") for _, row in tom.iterrows()]
ask_book = [book_side(row, "ask") for _, row in tom.iterrows()]

# ── parameters ────────────────────────────────────────────────────────────────
WINDOW = 20
NUM_STD = 2.0
POSITION_LIMIT = 20  # hard limit enforced by Prosperity
STOP_BUFFER = 15  # emergency exit if price moves this far past lower/upper
INITIAL_CAPITAL = 50_000


# ── helpers ───────────────────────────────────────────────────────────────────
def fill_buy_orders(ask_levels, limit_price, qty_wanted):
    """Simulate buying: match against ask levels at or below limit_price."""
    filled = 0
    total_cost = 0.0
    for price, vol in ask_levels:
        if price > limit_price or filled >= qty_wanted:
            break
        take = min(vol, qty_wanted - filled)
        filled += take
        total_cost += take * price
    return filled, total_cost


def fill_sell_orders(bid_levels, limit_price, qty_wanted):
    """Simulate selling: match against bid levels at or above limit_price."""
    filled = 0
    total_revenue = 0.0
    for price, vol in bid_levels:
        if price < limit_price or filled >= qty_wanted:
            break
        take = min(vol, qty_wanted - filled)
        filled += take
        total_revenue += take * price
    return filled, total_revenue


# ── main loop ─────────────────────────────────────────────────────────────────
position = 0
cash = 0.0
pnl_curve = []
trades = []
mean_arr = [None] * len(mids)
upper_arr = [None] * len(mids)
lower_arr = [None] * len(mids)

for i in range(WINDOW, len(mids)):
    w = mids[i - WINDOW : i]
    mean = w.mean()
    std = w.std()
    upper = mean + NUM_STD * std
    lower = mean - NUM_STD * std

    mean_arr[i] = mean
    upper_arr[i] = upper
    lower_arr[i] = lower

    bids = bid_book[i]  # [(price, vol), ...] best first
    asks = ask_book[i]

    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None

    # ── all orders are decided ONCE per tick (like Prosperity) ────────────
    # we collect intended orders, then simulate fills

    # 1) EXIT: passive limit at mean + emergency stop-loss
    if position > 0:
        exit_qty = position

        # passive exit: sell at mean (fills if bids >= mean)
        filled, revenue = fill_sell_orders(bids, int(mean), exit_qty)
        if filled > 0:
            cash += revenue
            position -= filled
            trades.append(
                {
                    "timestamp": timestamps[i],
                    "side": "EXIT_LONG",
                    "price": revenue / filled,
                    "size": filled,
                }
            )

        # emergency stop: if price drops far below lower band
        if position > 0 and best_bid is not None and best_bid < lower - STOP_BUFFER:
            filled, revenue = fill_sell_orders(bids, int(best_bid), position)
            if filled > 0:
                cash += revenue
                position -= filled
                trades.append(
                    {
                        "timestamp": timestamps[i],
                        "side": "STOP_LONG",
                        "price": revenue / filled,
                        "size": filled,
                    }
                )

    elif position < 0:
        exit_qty = abs(position)

        # passive exit: buy at mean (fills if asks <= mean)
        filled, cost = fill_buy_orders(asks, int(mean), exit_qty)
        if filled > 0:
            cash -= cost
            position += filled
            trades.append(
                {
                    "timestamp": timestamps[i],
                    "side": "EXIT_SHORT",
                    "price": cost / filled,
                    "size": filled,
                }
            )

        # emergency stop: if price rises far above upper band
        if position < 0 and best_ask is not None and best_ask > upper + STOP_BUFFER:
            filled, cost = fill_buy_orders(asks, int(best_ask), abs(position))
            if filled > 0:
                cash -= cost
                position += filled
                trades.append(
                    {
                        "timestamp": timestamps[i],
                        "side": "STOP_SHORT",
                        "price": cost / filled,
                        "size": filled,
                    }
                )

    # 2) ENTRY: only with room under position limit
    room_long = POSITION_LIMIT - position  # how much more we can buy
    room_short = POSITION_LIMIT + position  # how much more we can short

    if best_ask is not None and best_ask <= lower and room_long > 0:
        qty = min(room_long, sum(v for _, v in asks))
        filled, cost = fill_buy_orders(asks, int(lower), qty)
        if filled > 0:
            cash -= cost
            position += filled
            trades.append(
                {
                    "timestamp": timestamps[i],
                    "side": "BUY",
                    "price": cost / filled,
                    "size": filled,
                }
            )

    elif best_bid is not None and best_bid >= upper and room_short > 0:
        qty = min(room_short, sum(v for _, v in bids))
        filled, revenue = fill_sell_orders(bids, int(upper), qty)
        if filled > 0:
            cash += revenue
            position -= filled
            trades.append(
                {
                    "timestamp": timestamps[i],
                    "side": "SHORT",
                    "price": revenue / filled,
                    "size": filled,
                }
            )

    pnl_curve.append(cash + position * mids[i])

# ── results ───────────────────────────────────────────────────────────────────
final_pnl = cash + position * mids[-1]
equity = [INITIAL_CAPITAL + p for p in pnl_curve]

trade_df = pd.DataFrame(trades)
print(f"Final PnL:       {final_pnl:+,.0f}")
print(f"Total trades:    {len(trades)}")
print(f"Final position:  {position}")
if len(trades) > 0:
    print("\nTrade breakdown:")
    print(trade_df.groupby("side")[["size"]].agg(["count", "sum"]))

max_equity = max(equity)
max_dd = max_equity - min(equity[equity.index(max_equity) :]) if equity else 0
print(f"\nMax drawdown:    {max_dd:,.0f}")

# ── plot ──────────────────────────────────────────────────────────────────────
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 9))

ax1.plot(timestamps, mids, label="Mid Price", linewidth=1, color="steelblue")
ax1.plot(timestamps, mean_arr, label="Mean", linestyle="--", color="orange")
ax1.plot(timestamps, upper_arr, label="Upper Band", linestyle=":", color="red")
ax1.plot(timestamps, lower_arr, label="Lower Band", linestyle=":", color="green")

colors = {
    "BUY": "green",
    "SHORT": "red",
    "EXIT_LONG": "lime",
    "EXIT_SHORT": "orange",
    "STOP_LONG": "darkred",
    "STOP_SHORT": "darkgreen",
}
markers = {
    "BUY": "^",
    "SHORT": "v",
    "EXIT_LONG": "x",
    "EXIT_SHORT": "x",
    "STOP_LONG": "D",
    "STOP_SHORT": "D",
}

for side in colors:
    t = [x for x in trades if x["side"] == side]
    if t:
        ax1.scatter(
            [x["timestamp"] for x in t],
            [x["price"] for x in t],
            color=colors[side],
            marker=markers[side],
            zorder=5,
            s=90,
            linewidths=2,
            label=side,
        )

for day_boundary in tom["global_timestamp"].values[tom["day"].diff().ne(0).values]:
    ax1.axvline(day_boundary, color="gray", linestyle="--", linewidth=0.8)

ax1.set_title(
    f"TOMATOES — Bollinger Bands (window={WINDOW}, {NUM_STD}σ) | Limit-Order Fill Simulation"
)
ax1.set_ylabel("Price")
ax1.legend(loc="upper right", fontsize=8)

ax2.plot(timestamps[WINDOW:], equity, color="purple", linewidth=1.5)
ax2.axhline(
    INITIAL_CAPITAL,
    color="black",
    linestyle="--",
    linewidth=0.8,
    label="Starting capital",
)
ax2.fill_between(
    timestamps[WINDOW:],
    INITIAL_CAPITAL,
    equity,
    where=[e >= INITIAL_CAPITAL for e in equity],
    alpha=0.15,
    color="green",
)
ax2.fill_between(
    timestamps[WINDOW:],
    INITIAL_CAPITAL,
    equity,
    where=[e < INITIAL_CAPITAL for e in equity],
    alpha=0.15,
    color="red",
)
ax2.set_title(f"Equity Curve  |  Final PnL: {final_pnl:+,.0f}")
ax2.set_xlabel("Global Timestamp")
ax2.set_ylabel("Portfolio Value")
ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))
ax2.legend()
plt.tight_layout()
plt.show()
print("\nPlot saved.")
