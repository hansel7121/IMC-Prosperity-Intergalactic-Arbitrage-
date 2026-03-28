import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv(
    "/Users/hanselchen/Desktop/IMC Prosperity/prices_round_0_combined.csv",
    sep=";",
)
tom = (
    df[df["product"] == "EMERALDS"]
    .sort_values(["day", "timestamp"])
    .reset_index(drop=True)
)
tom["global_timestamp"] = (tom["day"] - tom["day"].min()) * 1_000_000 + tom["timestamp"]

mids = tom["mid_price"].values
timestamps = tom["global_timestamp"].values

plt.figure(figsize=(16, 5))
plt.plot(timestamps, mids, linewidth=1, color="steelblue")
plt.axvline(
    1_000_000, color="gray", linestyle="--", linewidth=0.8, label="Day boundary"
)
plt.title("EMERALDS — Mid Price")
plt.xlabel("Global Timestamp")
plt.ylabel("Mid Price")
plt.ylim(mids.min() - 2, mids.max() + 2)
plt.legend()
plt.tight_layout()
plt.show()
