"""
Explanation layer: template-based, experience-level-aware rationale.

Deterministic (no LLM). Depth scales with experienceLevel:
  beginner     - plain-language summary + a few simple bullets
  intermediate - adds the objective name + key backtest numbers
  advanced/expert - adds optimizer objective, shrinkage, BL-view status, caps

Always surfaces warnings[] (low emergency fund, relaxed filters, etc.).
"""

from __future__ import annotations

from typing import Dict, List, Optional

_OBJECTIVE_LABEL = {
    "max_sharpe": "maximum risk-adjusted return (max-Sharpe)",
    "min_variance": "lowest overall risk (minimum-variance)",
    "risk_parity": "balanced risk contribution (risk-parity)",
}


def build_explanation(
    objective: str,
    experience_level: Optional[str],
    weights: Dict[str, float],
    backtest: Optional[dict],
    applied_constraints: dict,
    lstm_view_applied: bool,
    warnings: List[str],
) -> dict:
    level = (experience_level or "beginner").strip().lower()
    if level not in {"beginner", "intermediate", "advanced", "expert"}:
        level = "beginner"

    n = len([w for w in weights.values() if w > 1e-6])
    top = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:3]
    top_str = ", ".join(f"{t} ({w*100:.0f}%)" for t, w in top if w > 1e-6)

    obj_label = _OBJECTIVE_LABEL.get(objective, objective)
    summary = (
        f"A diversified portfolio of {n} instrument(s) built to target {obj_label}."
    )

    bullets: List[str] = [
        f"Spread across {n} holdings to avoid over-concentration.",
        f"Largest positions: {top_str}." if top_str else "Weights balanced across holdings.",
    ]

    if level in {"intermediate", "advanced", "expert"} and backtest:
        bullets.append(
            f"Historical backtest ({backtest.get('method', 'n/a')}): "
            f"CAGR {backtest.get('cagr')}%, max drawdown {backtest.get('maxDrawdown')}%, "
            f"Sharpe {backtest.get('sharpe')}."
        )

    if level in {"advanced", "expert"}:
        bullets.append(
            f"Optimizer objective: {objective}; covariance via Ledoit-Wolf shrinkage; "
            f"per-asset cap and asset-class caps applied where specified."
        )
        if applied_constraints.get("asset_class_caps"):
            bullets.append(f"Asset-class caps: {applied_constraints['asset_class_caps']}.")
        bullets.append(
            "LSTM return-view (Black-Litterman): "
            + ("ON (experimental tilt applied)." if lstm_view_applied else "OFF (historical prior only).")
        )

    # Past-performance caveat is always present (also enforced via the disclaimer).
    bullets.append("Past performance does not guarantee future results.")

    return {
        "summary": summary,
        "bullets": bullets,
        "experienceLevel": level,
        "warnings": list(warnings or []),
    }
