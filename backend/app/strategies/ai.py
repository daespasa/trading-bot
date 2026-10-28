"""ML-driven strategies. By default they only trade while their model kind has an ACTIVE model (passed the
out-of-sample validation gate). With require_validation=0 they also trade unvalidated models, as an explicit
live experiment; those signals are flagged `unvalidated_model` so results are never mistaken for a validated edge."""
from app.strategies.base import Context, Strategy, directional_signal


class MLStrategy(Strategy):
    family = "ml"
    model_kind = ""
    defaults = {"trade_usdc": 5, "min_edge": 0.05, "max_spread": 0.04, "max_prediction_age_min": 20,
                "min_hours_left": 1, "require_validation": 1, "max_edge": 0.25, "min_price": 0.03}

    def evaluate(self, ctx: Context, cfg: dict):
        out = []
        for m in ctx.markets.values():
            pred = ctx.predictions.get((self.model_kind, m.id))
            hl = ctx.hours_left(m)
            if not pred or len(m.tokens) != 2 or m.closed or hl is None or hl < cfg["min_hours_left"]:
                continue
            if (ctx.now - pred["ts"]).total_seconds() > cfg["max_prediction_age_min"] * 60:
                continue
            p = pred["p"]
            for idx, pf in ((0, p), (1, 1 - p)):
                s = directional_signal(self.name, ctx, m, idx, pf, cfg,
                                       {"model_id": pred["model_id"], "model_status": pred["status"],
                                        "p_model": round(p, 4), "p_market": pred["p_market"]})
                if s and s.actionable:
                    ask = float(s.details["ask"])
                    if ask < cfg["min_price"]:
                        s.actionable, s.reason = False, f"ask {ask} below min_price {cfg['min_price']}: longshot"
                    elif s.net_edge * ask > cfg["max_edge"]:
                        # model miscalibration at the extremes, not a real 25+ point mispricing
                        s.actionable, s.reason = False, f"edge above max_edge {cfg['max_edge']}: treated as model error"
                if s:
                    if pred["status"] != "ACTIVE":
                        if cfg["require_validation"] and s.actionable:
                            s.actionable, s.reason = False, f"model not active: {pred['status']}"
                        elif not cfg["require_validation"]:
                            s.details["unvalidated_model"] = True
                            s.risk = "very high"
                    out.append(s)
        return out


class LightGBMStrategy(MLStrategy):
    name = "ai_lightgbm"
    model_kind = "lightgbm"
    description = "Buys an outcome when the calibrated LightGBM probability exceeds ask + fee + min_edge."


class LogisticStrategy(MLStrategy):
    name = "ai_logistic"
    model_kind = "logistic"
    description = "Same rule with the logistic-regression baseline model."
