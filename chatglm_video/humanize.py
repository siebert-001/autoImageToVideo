"""在关键步骤之间加入随机间隔，降低固定节拍特征（不能替代合规与限速）。"""

import random
import time


def sleep_jittered(base: float, spread: float) -> None:
    """在 [base - spread, base + spread] 内均匀随机睡眠，spread 过大时下限不低于约 0.05s。"""
    lo = max(0.05, base - spread)
    hi = base + spread
    time.sleep(random.uniform(lo, hi))


def sleep_uniform_range(lo: float, hi: float) -> None:
    """在闭区间 [min(lo,hi), max(lo,hi)] 内均匀随机睡眠；上下限不低于约 0.05s。"""
    a, b = (lo, hi) if lo <= hi else (hi, lo)
    a = max(0.05, float(a))
    b = max(a, float(b))
    time.sleep(random.uniform(a, b))
