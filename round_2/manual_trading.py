import numpy as np

def research(x):
    return 200000 * np.log(1 + x) / np.log(101)

def scale(x):
    return 7 * (x / 100)

def speed():
    return 0.5

for i in range(1, 101):
    re = research(i)
    sc = scale(100 - i)
    pnl = re * sc - 50000
    print(f"Research: {i} - {round(re)}, Scale: {100-i} - {round(sc)}, PnL: {round(pnl)}")



def calculate_pnl(research, scale, speed):
    return (research * scale * speed) - 50000