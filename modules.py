import matplotlib.pyplot as plt
import numpy as np
import yfinance as yf
import pandas as pd


def view_price(path, symbol):
    df = pd.read_csv(
        path,
        sep=";",
    )

    tomatoes = df[df["symbol"] == symbol]
    plt.plot(tomatoes["timestamp"], tomatoes["price"])
    plt.title(symbol)
    plt.xlabel("Timestamp")
    plt.ylabel("Price")

    plt.tight_layout()
    plt.show()


def concat(path1, path2, name, symbol):
    # path1 = day -2 (earlier), path2 = day -1 (later)
    df1 = pd.read_csv(path1, sep=";")  # day -2
    df2 = pd.read_csv(path2, sep=";")  # day -1

    # offset day -1 so it comes AFTER day -2
    df2["timestamp"] += df1["timestamp"].max()

    df = pd.concat([df1, df2], ignore_index=True)
    df = df[df["symbol"] == symbol]  # <-- filter here
    df = df.sort_values("timestamp").reset_index(drop=True)
    df.to_csv(name, index=False)
    return df
