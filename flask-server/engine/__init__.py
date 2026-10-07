"""
Phase 3 recommendation engine (MPT + backtesting + Black-Litterman view).

Modules:
    optimizer      - convex MPT optimization (max-Sharpe / min-variance / risk-parity)
    constraints    - map optional user preferences to filters/caps/tilts/warnings
    blacklitterman - optional LSTM-view posterior expected returns (off by default)
    backtest       - weight-replay + walk-forward historical performance metrics
    montecarlo     - portfolio-level Monte Carlo corpus projection
    explain        - template, experience-level-aware rationale
    recommend      - orchestrator producing the full response contract

The engine consumes the Phase 2 model/metrics store (expected returns +
Ledoit-Wolf covariance) and never trains a model or hits a data provider in the
request path. It does NOT import TensorFlow.
"""


class InfeasibleError(Exception):
    """Raised when constraints make the optimization infeasible or the universe
    empty. The API layer maps this to HTTP 422 with a helpful message."""
