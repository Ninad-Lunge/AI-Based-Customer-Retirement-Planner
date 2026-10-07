import React from 'react';
import { useNavigate } from 'react-router-dom';
import './Body0.css';

const FEATURES = [
  { title: 'AI-Powered', desc: 'LSTM models analyze 5-year trends.' },
  { title: 'Risk-Tailored', desc: 'Portfolios matched to your appetite.' },
  { title: 'Goal-Aware', desc: 'Projects whether you hit your target.' },
];

const STEPS = [
  {
    n: '1',
    title: 'Tell us your goals',
    desc: 'Your age, target corpus, monthly amount and risk appetite — plus an optional yearly step-up to grow your SIP over time.',
  },
  {
    n: '2',
    title: 'We optimize a portfolio',
    desc: 'A Modern Portfolio Theory optimizer builds a diversified allocation from historical returns and a shrinkage covariance estimate.',
  },
  {
    n: '3',
    title: 'See the evidence',
    desc: 'A historical backtest and a Monte Carlo projection show the likely range of outcomes and your probability of hitting the goal.',
  },
];

const Body0 = () => {
  const navigate = useNavigate();

  return (
    <>
    <section className="hero-section cont">
      <div className="hero-grid">
        <div className="hero-copy">
          <span className="hero-pill">
            <span className="hero-pill-dot" /> AI retirement planning
          </span>
          <h1 className="hero-title">
            Invest smarter,<br />
            <span className="hero-title-grad">retire stronger.</span>
          </h1>
          <p className="hero-sub">
            Get a data-driven, risk-tailored investment strategy that charts a clear
            path to your retirement goal — built on machine-learning price models.
          </p>

          <div className="hero-actions">
            <button type="button" className="ui-btn ui-btn-primary" onClick={() => navigate('/Plan')}>
              Plan Now
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            <a className="ui-btn ui-btn-ghost" href="#how-it-works">How it works</a>
          </div>

          <ul className="hero-features">
            {FEATURES.map((f) => (
              <li key={f.title} className="hero-feature">
                <span className="hero-feature-check" aria-hidden="true">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                    <path d="M20 6 9 17l-5-5" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </span>
                <div>
                  <strong>{f.title}</strong>
                  <span>{f.desc}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>

        {/* Decorative, self-contained SVG illustration (no image assets) */}
        <div className="hero-art" aria-hidden="true">
          <div className="hero-art-card">
            <div className="hero-art-head">
              <span>Projected corpus</span>
              <span className="hero-art-badge">On track</span>
            </div>
            <div className="hero-art-value">₹1.68 Cr</div>
            <svg className="hero-art-chart" viewBox="0 0 320 140" preserveAspectRatio="none">
              <defs>
                <linearGradient id="fillGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#6366f1" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="#6366f1" stopOpacity="0" />
                </linearGradient>
              </defs>
              <path d="M0 120 C 50 110, 70 70, 110 72 S 180 40, 220 34 S 290 14, 320 8 L 320 140 L 0 140 Z" fill="url(#fillGrad)" />
              <path d="M0 120 C 50 110, 70 70, 110 72 S 180 40, 220 34 S 290 14, 320 8" fill="none" stroke="#4f46e5" strokeWidth="3" strokeLinecap="round" />
            </svg>
            <div className="hero-art-bars">
              <span style={{ height: '38%' }} />
              <span style={{ height: '62%' }} />
              <span style={{ height: '48%' }} />
              <span style={{ height: '80%' }} />
              <span style={{ height: '66%' }} />
            </div>
          </div>
          <div className="hero-art-chip hero-art-chip-1">+16.5% avg return</div>
          <div className="hero-art-chip hero-art-chip-2">Low · Med · High</div>
        </div>
      </div>
    </section>

    {/* How it works — target for the hero "How it works" anchor */}
    <section id="how-it-works" className="how-section cont">
      <h2 className="how-title">How it works</h2>
      <p className="how-sub">From your goals to an evidence-backed plan in three steps.</p>
      <ol className="how-steps">
        {STEPS.map((s) => (
          <li key={s.n} className="how-step">
            <span className="how-step-num">{s.n}</span>
            <div>
              <strong>{s.title}</strong>
              <span>{s.desc}</span>
            </div>
          </li>
        ))}
      </ol>
    </section>
    </>
  );
};

export default Body0;
