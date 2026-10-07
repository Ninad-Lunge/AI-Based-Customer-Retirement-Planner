import React, { useState } from 'react';
import axios from 'axios';
import './PlanInput.css';
import InvestmentResult from './InvestmentResult';

const API_URL = process.env.REACT_APP_API_URL || 'http://127.0.0.1:5000';

const RISK_OPTIONS = [
  { value: 'low', label: 'Low', hint: 'Stability first' },
  { value: 'medium', label: 'Medium', hint: 'Balanced growth' },
  { value: 'high', label: 'High', hint: 'Max growth' },
];

const FIELDS = [
  { name: 'currentAge', label: 'Current Age', placeholder: 'e.g. 30', min: 18, max: 100 },
  { name: 'retirementAge', label: 'Retirement Age', placeholder: 'e.g. 60', min: 19, max: 100 },
  { name: 'desiredFund', label: 'Desired Retirement Fund', placeholder: 'e.g. 10000000', min: 1, prefix: '₹' },
  { name: 'monthlyInvestment', label: 'Monthly Investment', placeholder: 'e.g. 15000', min: 1, prefix: '₹' },
  {
    name: 'stepUpPercent',
    label: 'Yearly Step-Up (optional)',
    placeholder: 'e.g. 10',
    min: 0,
    max: 25,
    suffix: '%',
    optional: true,
    hint: 'Increase your monthly amount by this % each year to reach goals faster.',
  },
];

const PlanInput = () => {
  const [formData, setFormData] = useState({
    currentAge: '',
    retirementAge: '',
    desiredFund: '',
    monthlyInvestment: '',
    riskCategory: '',
    stepUpPercent: '',
  });

  const [validationErrors, setValidationErrors] = useState({});
  const [investmentStrategy, setInvestmentStrategy] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const validate = () => {
    const errors = {};
    const currentAge = Number(formData.currentAge);
    const retirementAge = Number(formData.retirementAge);
    const desiredFund = Number(formData.desiredFund);
    const monthlyInvestment = Number(formData.monthlyInvestment);

    if (!formData.currentAge || isNaN(currentAge)) errors.currentAge = 'Current age is required.';
    else if (currentAge < 18 || currentAge > 100) errors.currentAge = 'Must be between 18 and 100.';

    if (!formData.retirementAge || isNaN(retirementAge)) errors.retirementAge = 'Retirement age is required.';
    else if (retirementAge > 100) errors.retirementAge = 'Must be 100 or less.';
    else if (currentAge && retirementAge <= currentAge) errors.retirementAge = 'Must be greater than current age.';

    if (!formData.desiredFund || isNaN(desiredFund)) errors.desiredFund = 'Desired fund is required.';
    else if (desiredFund <= 0) errors.desiredFund = 'Must be greater than 0.';

    if (!formData.monthlyInvestment || isNaN(monthlyInvestment)) errors.monthlyInvestment = 'Monthly investment is required.';
    else if (monthlyInvestment <= 0) errors.monthlyInvestment = 'Must be greater than 0.';

    if (!formData.riskCategory) errors.riskCategory = 'Please select a risk preference.';

    // Optional: yearly step-up. Only validated if the user entered something.
    if (formData.stepUpPercent !== '' && formData.stepUpPercent !== null && formData.stepUpPercent !== undefined) {
      const stepUp = Number(formData.stepUpPercent);
      if (isNaN(stepUp) || stepUp < 0 || stepUp > 25) errors.stepUpPercent = 'Must be between 0 and 25.';
    }

    return errors;
  };

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
    if (validationErrors[name]) {
      setValidationErrors((prev) => ({ ...prev, [name]: '' }));
    }
  };

  const selectRisk = (value) => {
    setFormData((prev) => ({ ...prev, riskCategory: value }));
    if (validationErrors.riskCategory) {
      setValidationErrors((prev) => ({ ...prev, riskCategory: '' }));
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const errors = validate();
    if (Object.keys(errors).length > 0) {
      setValidationErrors(errors);
      return;
    }
    setValidationErrors({});
    setLoading(true);
    setError(null);
    try {
      // Build the payload: send stepUpPercent only when provided (empty -> omit,
      // so the backend applies its default of 0 and the base flow is unchanged).
      const payload = { ...formData };
      if (payload.stepUpPercent === '' || payload.stepUpPercent === null || payload.stepUpPercent === undefined) {
        delete payload.stepUpPercent;
      } else {
        payload.stepUpPercent = Number(payload.stepUpPercent);
      }
      const response = await axios.post(`${API_URL}/investment-strategy`, payload);
      setInvestmentStrategy(response.data);
    } catch (err) {
      const message =
        err.response?.data?.error ||
        err.response?.data?.message ||
        err.message ||
        'An unexpected error occurred. Please try again.';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  const handleClearResults = () => {
    setInvestmentStrategy(null);
    setError(null);
  };

  return (
    <section className="plan-section cont">
      <div className="plan-card">
        {loading && (
          <div className="plan-overlay" role="status" aria-live="polite">
            <div className="plan-spinner" />
            <span>Calculating your strategy…</span>
          </div>
        )}

        <header className="plan-head">
          <h2>Plan your retirement</h2>
          <p>Tell us about your goals and we'll build a tailored investment strategy.</p>
        </header>

        {error && (
          <div className="plan-alert" role="alert">
            <span>{error}</span>
            <button type="button" onClick={() => setError(null)} aria-label="Dismiss error">×</button>
          </div>
        )}

        <form className="plan-form" onSubmit={handleSubmit} noValidate>
          <div className="plan-fields">
            {FIELDS.map((f) => (
              <div className="field" key={f.name}>
                <label htmlFor={f.name}>{f.label}</label>
                <div className={`field-input${f.prefix ? ' has-prefix' : ''}${f.suffix ? ' has-suffix' : ''}${validationErrors[f.name] ? ' invalid' : ''}`}>
                  {f.prefix && <span className="field-prefix">{f.prefix}</span>}
                  <input
                    type="number"
                    id={f.name}
                    name={f.name}
                    placeholder={f.placeholder}
                    value={formData[f.name]}
                    onChange={handleChange}
                    min={f.min}
                    max={f.max}
                    aria-invalid={!!validationErrors[f.name]}
                    aria-describedby={validationErrors[f.name] ? `${f.name}-error` : undefined}
                  />
                  {f.suffix && <span className="field-suffix">{f.suffix}</span>}
                </div>
                {f.hint && !validationErrors[f.name] && (
                  <div className="field-hint">{f.hint}</div>
                )}
                {validationErrors[f.name] && (
                  <div id={`${f.name}-error`} className="field-error" role="alert">
                    {validationErrors[f.name]}
                  </div>
                )}
              </div>
            ))}
          </div>

          <div className="field">
            <label>Risk Preference</label>
            <div
              className={`risk-group${validationErrors.riskCategory ? ' invalid' : ''}`}
              role="radiogroup"
              aria-label="Risk preference"
            >
              {RISK_OPTIONS.map((opt) => (
                <button
                  type="button"
                  key={opt.value}
                  role="radio"
                  aria-checked={formData.riskCategory === opt.value}
                  className={`risk-chip risk-${opt.value}${formData.riskCategory === opt.value ? ' selected' : ''}`}
                  onClick={() => selectRisk(opt.value)}
                >
                  <span className="risk-label">{opt.label}</span>
                  <span className="risk-hint">{opt.hint}</span>
                </button>
              ))}
            </div>
            {validationErrors.riskCategory && (
              <div className="field-error" role="alert">{validationErrors.riskCategory}</div>
            )}
          </div>

          <button type="submit" className="ui-btn ui-btn-primary plan-submit" disabled={loading}>
            {loading ? 'Calculating…' : 'Generate Strategy'}
          </button>
        </form>

        {investmentStrategy && (
          <>
            <InvestmentResult formData={formData} investmentStrategy={investmentStrategy} />
            <div className="plan-clear">
              <button type="button" className="ui-btn ui-btn-ghost" onClick={handleClearResults}>
                Clear Results
              </button>
            </div>
          </>
        )}
      </div>
    </section>
  );
};

export default PlanInput;
