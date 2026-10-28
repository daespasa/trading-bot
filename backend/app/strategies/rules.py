"""Rule-based directional strategies. Both are explicit, testable formulas; neither is assumed profitable."""
import math
import re

from app.strategies.arbitrage import parse_threshold
from app.strategies.base import Context, Strategy, directional_signal


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def crypto_target(m) -> tuple[str, str] | None:
    """-> (symbol, 'above'|'updown') for BTC/ETH price markets we can model."""
    q = m.question.lower()
    sym = "BTC" if re.search(r"\bbitcoin\b|\bbtc\b", q) else "ETH" if re.search(r"\bethereum\b|\beth\b", q) else None
    if not sym or len(m.tokens) != 2 or not m.end_date:
        return None
    if "up or down" in q:
        return sym, "updown"
    t = parse_threshold(m.question)
    if t and t[0] == "above":
        return sym, "above"
    return None


def prob_twap_above(spot: float, strike: float, avg_so_far: float | None, frac_done: float, sigma_min: float,
                    minutes_left: float, drift_min: float = 0.0, basis_sd: float = 0.0) -> float:
    """P(time-weighted average over the window >= strike), knowing the average of the elapsed part.
    The remaining-part average of a Brownian path has sd ~ S*sigma*sqrt(t/3). `basis_sd` adds the
    uncertainty of using Binance as a proxy for the resolution feed (Chainlink)."""
    if minutes_left <= 0 or frac_done >= 1:
        return 1.0 if (avg_so_far if avg_so_far is not None else spot) >= strike else 0.0
    done = avg_so_far if avg_so_far is not None else spot
    needed = (strike - frac_done * done) / (1 - frac_done)
    mean = spot * (1 + drift_min * minutes_left / 2)
    sd = math.sqrt((spot * sigma_min) ** 2 * minutes_left / 3 + basis_sd ** 2)
    return norm_cdf((mean - needed) / sd) if sd > 0 else (1.0 if mean >= needed else 0.0)


def prob_above(spot: float, strike: float, sigma_min: float, minutes: float, drift_min: float = 0.0) -> float:
    """P(S_T > K) for log-normal S with per-minute vol sigma and drift: Phi((ln(S/K) + (mu - s^2/2)t) / (s sqrt t))."""
    if minutes <= 0 or sigma_min <= 0:
        return 1.0 if spot > strike else 0.0
    sd = sigma_min * math.sqrt(minutes)
    return norm_cdf((math.log(spot / strike) + (drift_min - 0.5 * sigma_min ** 2) * minutes) / sd)


class CryptoMomentum(Strategy):
    name = "crypto_momentum"
    family = "rule"
    description = ("BTC/ETH price markets. Fair P(YES) = Phi((ln(S/K) + mu*t)/(sigma*sqrt t)) using Binance spot S, "
                   "strike or reference candle K, realised 1m vol sigma and a momentum drift "
                   "mu = momentum_weight * (return over lookback)/lookback. Trades when fair - ask - fee > min_edge.")
    defaults = {"trade_usdc": 5, "min_edge": 0.04, "max_edge": 0.25, "max_spread": 0.04, "vol_window_min": 240,
                "lookback_min": 30, "momentum_weight": 0.5, "min_minutes_left": 3, "max_hours_left": 48,
                "proxy_basis_bps": 3}

    def evaluate(self, ctx: Context, cfg: dict):
        feed = ctx.crypto
        if feed is None or not feed.fresh():
            return []
        out = []
        for m in ctx.markets.values():
            tgt = crypto_target(m)
            if not tgt or m.closed:
                continue
            sym, kind = tgt
            minutes = (m.end_date - ctx.now).total_seconds() / 60
            if minutes < cfg["min_minutes_left"] or minutes > cfg["max_hours_left"] * 60:
                continue
            spot, sigma = feed.spot(sym), feed.vol_per_minute(sym, cfg["vol_window_min"])
            r = feed.ret(sym, cfg["lookback_min"])
            if spot is None or sigma is None or r is None:
                continue
            twap = "twap" in (m.description or "").lower()
            drift = cfg["momentum_weight"] * r / cfg["lookback_min"]
            details = {"symbol": sym, "sigma_1m": sigma, "minutes_left": round(minutes, 1), "recent_return": r,
                       "spot": spot, "reference": "Binance proxy for Chainlink TWAP" if twap else "Binance (resolution source)"}
            if kind == "above":
                strike = parse_threshold(m.question)[1]
                p = prob_above(spot, strike, sigma, minutes, drift)
            else:
                if not m.event_start or m.event_start > ctx.now:
                    continue
                start_ms = int(m.event_start.timestamp() * 1000)
                ref = ctx.refs.get((sym, start_ms))
                strike = ref and (ref["open"] if twap else ref["close"])
                if strike is None:
                    continue
                if twap:
                    total = (m.end_date - m.event_start).total_seconds() / 60
                    wa = feed.window_average(sym, start_ms, int(ctx.now.timestamp() * 1000))
                    frac = min(wa[1] / total, 1.0) if wa else 0.0
                    p = prob_twap_above(spot, strike, wa[0] if wa else None, frac, sigma, minutes, drift,
                                        basis_sd=spot * cfg["proxy_basis_bps"] / 1e4)
                    details["twap_so_far"], details["window_done"] = (wa[0] if wa else None), round(frac, 3)
                else:
                    p = prob_above(spot, strike, sigma, minutes, drift)
            details["strike"] = strike
            for idx, pf in ((0, p), (1, 1 - p)):
                s = directional_signal(self.name, ctx, m, idx, pf, cfg, details)
                if s and s.actionable and s.net_edge * float(s.details["ask"]) > cfg["max_edge"]:
                    # A huge edge in a liquid crypto market almost always means our model/reference is wrong.
                    s.actionable, s.reason = False, f"edge above max_edge {cfg['max_edge']}: treated as model error"
                if s:
                    out.append(s)
        return out


class MeanReversion(Strategy):
    name = "mean_reversion"
    family = "rule"
    description = ("If an outcome's mid fell by >= move_threshold within window_min (and the market has > min_hours_left), "
                   "buy it expecting partial reversion of revert_fraction of the move. Exits: take-profit, stop-loss, "
                   "time stop. Fair price = current mid + revert_fraction * move.")
    defaults = {"trade_usdc": 5, "move_threshold": 0.06, "window_min": 60, "revert_fraction": 0.4, "min_edge": 0.02,
                "max_spread": 0.03, "min_hours_left": 24, "take_profit": 0.04, "stop_loss": 0.06,
                "max_hold_minutes": 360}

    def evaluate(self, ctx: Context, cfg: dict):
        out = []
        horizon = ctx.now.timestamp() - cfg["window_min"] * 60
        for m in ctx.markets.values():
            hl = ctx.hours_left(m)
            if len(m.tokens) != 2 or m.closed or hl is None or hl < cfg["min_hours_left"]:
                continue
            for idx, tok in enumerate(m.tokens):
                h = ctx.history.get(tok)
                if not h or len(h) < 3:
                    continue
                past = [mid for ts, mid in h if ts >= horizon]
                if len(past) < 3:
                    continue
                peak, now_mid = max(past), past[-1]
                move = peak - now_mid
                if move < cfg["move_threshold"]:
                    continue
                fair = now_mid + cfg["revert_fraction"] * move
                s = directional_signal(self.name, ctx, m, idx, fair, cfg,
                                       {"peak_mid": peak, "mid": now_mid, "move": round(move, 4)},
                                       exit_rules={k: cfg[k] for k in ("take_profit", "stop_loss", "max_hold_minutes")})
                if s:
                    out.append(s)
        return out
