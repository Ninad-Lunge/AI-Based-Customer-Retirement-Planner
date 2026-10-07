import React from 'react';
import './InvestmentResult.css';

// Distinct colors for allocation segments (cycles if more than 10 stocks).
const PALETTE = [
  '#4f6ef7', '#1a9e6c', '#f2994a', '#9b51e0', '#eb5757',
  '#2d9cdb', '#f2c94c', '#27ae60', '#e07a5f', '#6c5ce7',
];

const formatINR = (value) => {
  const num = Number(value);
  if (Number.isNaN(num)) return value;
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(num);
};

const capitalize = (str) => (str ? str.charAt(0).toUpperCase() + str.slice(1) : '');

const riskClass = (profile) => {
  const p = (profile || '').toLowerCase();
  if (p.includes('high')) return 'high';
  if (p.includes('medium')) return 'medium';
  return 'low';
};

const InvestmentResult = ({ formData, investmentStrategy }) => {
  if (!investmentStrategy) return null;

  // ---- Error / domain-failure state ----
  if (investmentStrategy.error) {
    return (
      <div className="ir-wrap">
        <div className="ir-error">
          <strong>Unable to generate a strategy. </strong>
          {investmentStrategy.error}
        </div>
      </div>
    );
  }

  const suggestions = investmentStrategy['Investment Suggestions'];
  if (!Array.isArray(suggestions) || suggestions.length === 0) {
    return (
      <div className="ir-wrap">
        <div className="ir-error">
          The server returned an unexpected response. Please try again.
        </div>
      </div>
    );
  }

  const goalAchievable = investmentStrategy['Goal Achievable'];
  const totalFV = investmentStrategy['Total Future Value (INR)'];
  const avgReturn = investmentStrategy['Average Annual Return (%)'];
  const varINR = investmentStrategy['Value at Risk (5th percentile) (INR)'];
  const optimistic = investmentStrategy['Optimistic Value (90th percentile) (INR)'];
  const avgSim = investmentStrategy['Average Value from Simulation (INR)'];

  return (
    <div className="ir-wrap">
      {/* ---- Goal status banner ---- */}
      <div className={`ir-goal-banner ${goalAchievable ? 'achieved' : 'missed'}`}>
        <span className="ir-goal-icon">{goalAchievable ? '✓' : '⚠'}</span>
        <span>
          {goalAchievable
            ? `On track! Your projected corpus of ${formatINR(totalFV)} meets your goal of ${formatINR(formData.desiredFund)}.`
            : `Projected corpus of ${formatINR(totalFV)} falls short of your ${formatINR(formData.desiredFund)} goal. Consider increasing your monthly investment or horizon.`}
        </span>
      </div>

      {/* ---- Key stats ---- */}
      <div className="ir-stats">
        <div className="ir-stat-card">
          <div className="ir-stat-label">Projected Corpus</div>
          <div className="ir-stat-value">{formatINR(totalFV)}</div>
          <div className="ir-stat-sub">at retirement</div>
        </div>
        <div className="ir-stat-card">
          <div className="ir-stat-label">Avg Annual Return</div>
          <div className={`ir-stat-value ${avgReturn >= 0 ? 'pos' : 'neg'}`}>{avgReturn}%</div>
          <div className="ir-stat-sub">blended portfolio</div>
        </div>
        {optimistic != null && (
          <div className="ir-stat-card">
            <div className="ir-stat-label">Optimistic (P90)</div>
            <div className="ir-stat-value pos">{formatINR(optimistic)}</div>
            <div className="ir-stat-sub">top 10% of outcomes</div>
          </div>
        )}
        {varINR != null && (
          <div className="ir-stat-card">
            <div className="ir-stat-label">Downside (P5)</div>
            <div className="ir-stat-value neg">{formatINR(varINR)}</div>
            <div className="ir-stat-sub">worst 5% of outcomes</div>
          </div>
        )}
        {avgSim != null && (
          <div className="ir-stat-card">
            <div className="ir-stat-label">Simulation Avg</div>
            <div className="ir-stat-value">{formatINR(avgSim)}</div>
            <div className="ir-stat-sub">10k Monte Carlo runs</div>
          </div>
        )}
      </div>

      {/* ---- Allocation visualization ---- */}
      <div>
        <div className="ir-section-title">Recommended Allocation</div>
        <div className="ir-alloc-bar" role="img" aria-label="Portfolio allocation breakdown">
          {suggestions.map((s, i) => (
            <div
              key={s['Stock Name']}
              className="ir-alloc-seg"
              style={{
                width: `${s['Investment Percentage']}%`,
                backgroundColor: PALETTE[i % PALETTE.length],
              }}
              title={`${s['Stock Name']}: ${s['Investment Percentage']}%`}
            />
          ))}
        </div>
        <div className="ir-alloc-legend">
          {suggestions.map((s, i) => (
            <div className="ir-legend-item" key={s['Stock Name']}>
              <span
                className="ir-legend-dot"
                style={{ backgroundColor: PALETTE[i % PALETTE.length] }}
              />
              {s['Stock Name']} ({s['Investment Percentage']}%)
            </div>
          ))}
        </div>
      </div>

      {/* ---- Detailed suggestions ---- */}
      <div>
        <div className="ir-section-title">Investment Breakdown</div>
        <table className="ir-table">
          <thead>
            <tr>
              <th scope="col">Asset</th>
              <th scope="col">Allocation</th>
              <th scope="col">Exp. Return</th>
              <th scope="col">Risk</th>
              <th scope="col">Projected Value</th>
            </tr>
          </thead>
          <tbody>
            {suggestions.map((s, i) => (
              <tr key={s['Stock Name']}>
                <td>
                  <div className="ir-ticker">{s['Stock Name']}</div>
                  {s['Asset Class'] && (
                    <div className="ir-asset-class">{s['Asset Class']}</div>
                  )}
                </td>
                <td>
                  <div className="ir-pct-cell">
                    <div className="ir-pct-track">
                      <div
                        className="ir-pct-fill"
                        style={{
                          width: `${s['Investment Percentage']}%`,
                          background: PALETTE[i % PALETTE.length],
                        }}
                      />
                    </div>
                    <span>{s['Investment Percentage']}%</span>
                  </div>
                </td>
                <td>{s['Annual Return (%)']}%</td>
                <td>
                  <span className={`ir-badge ${riskClass(s['Risk Profile'])}`}>
                    {s['Risk Profile']}
                  </span>
                </td>
                <td>{formatINR(s['Future Value (INR)'])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ---- Input summary ---- */}
      <div>
        <div className="ir-section-title">Your Plan</div>
        <div className="ir-input-summary">
          <div className="ir-input-chip">
            <div className="k">Current Age</div>
            <div className="v">{formData.currentAge}</div>
          </div>
          <div className="ir-input-chip">
            <div className="k">Retirement Age</div>
            <div className="v">{formData.retirementAge}</div>
          </div>
          <div className="ir-input-chip">
            <div className="k">Monthly</div>
            <div className="v">{formatINR(formData.monthlyInvestment)}</div>
          </div>
          <div className="ir-input-chip">
            <div className="k">Goal</div>
            <div className="v">{formatINR(formData.desiredFund)}</div>
          </div>
          <div className="ir-input-chip">
            <div className="k">Risk</div>
            <div className="v">{capitalize(formData.riskCategory)}</div>
          </div>
        </div>
      </div>

      <p className="ir-disclaimer">
        Projections are model estimates based on historical data and are not financial
        advice. Actual returns will vary.
      </p>
    </div>
  );
};

export default InvestmentResult;
