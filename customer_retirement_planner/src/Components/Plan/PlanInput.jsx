import React, { useState } from 'react';
import axios from 'axios';
import './PlanInput.css';
import form_filling from '../../Assets/Plan/form_filling.png';
import InvestmentResult from './InvestmentResult';

const PlanInput = () => {
  const [formData, setFormData] = useState({
    currentAge: '',
    retirementAge: '',
    desiredFund: '',
    monthlyInvestment: '',
    riskCategory: ''
  });

  const [investmentStrategy, setInvestmentStrategy] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData({
      ...formData,
      [name]: value
    });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      console.log("Sending request to server with data:", formData);
      const response = await axios.post('http://127.0.0.1:5000/investment-strategy', formData);
      console.log("Server response:", response.data);
      setInvestmentStrategy(response.data);
    } catch (error) {
      if (error.response) {
        console.error("Server responded with an error:", error.response.data);
      } else if (error.request) {
        console.error("No response received from server:", error.request);
      } else {
        console.error("Error setting up request:", error.message);
      }
      console.error("Axios error config:", error.config);
      setError(error);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return <div className="body0 cont" id="Home"><div className="body-text"><div>Loading...</div></div></div>;
  }

  if (error) {
    return (
      <div className="body0 cont" id="Home">
        <div className="body-text">
          <div>Error: {error.message}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="body0 cont" id="Home">
      <div className="body-text">
        <div className="card form-card">
          <div className="hero bb">
            <h3>Plan Your Retirement Smartly</h3>
            <p>Choose the best investment strategy to achieve your retirement goals.</p>
          </div>
          <form id="retirementForm" className="cont" onSubmit={handleSubmit}>
            <div className="form-group">
              <label htmlFor="currentAge">Current Age</label>
              <input 
                type="number" 
                className="form-control" 
                id="currentAge" 
                name="currentAge" 
                placeholder="Enter your current age" 
                value={formData.currentAge}
                onChange={handleChange}
                required 
              />
            </div>
            <div className="form-group">
              <label htmlFor="retirementAge">Retirement Age</label>
              <input 
                type="number" 
                className="form-control" 
                id="retirementAge" 
                name="retirementAge" 
                placeholder="Enter your retirement age" 
                value={formData.retirementAge}
                onChange={handleChange}
                required 
              />
            </div>
            <div className="form-group">
              <label htmlFor="desiredFund">Desired Retirement Fund (in INR)</label>
              <input 
                type="number" 
                className="form-control" 
                id="desiredFund" 
                name="desiredFund" 
                placeholder="Enter your desired retirement fund (in INR)" 
                value={formData.desiredFund}
                onChange={handleChange}
                required 
              />
            </div>
            <div className="form-group">
              <label htmlFor="monthlyInvestment">Monthly Investment (in INR)</label>
              <input 
                type="number" 
                className="form-control" 
                id="monthlyInvestment" 
                name="monthlyInvestment" 
                placeholder="Enter your monthly investment (in INR)" 
                value={formData.monthlyInvestment}
                onChange={handleChange}
                required 
              />
            </div>
            <div className="form-group">
              <label htmlFor="riskCategory">Risk Preference</label>
              <select 
                className="form-control" 
                id="riskCategory" 
                name="riskCategory" 
                value={formData.riskCategory}
                onChange={handleChange}
                required
              >
                <option value="" disabled>Choose your risk preference</option>
                <option value="high">High Risk-High Return</option>
                <option value="medium">Medium Risk-Medium Return</option>
                <option value="low">Low Risk-Low Return</option>
              </select>
            </div>
            <div className="form-group">
              <input type="submit" className="btn btn-primary btn-block" value="Submit" />
            </div>
          </form>
          {investmentStrategy && (
            <InvestmentResult formData={formData} investmentStrategy={investmentStrategy} />
          )}
        </div>
      </div>
      <div className='body0-img'>
        <img src={form_filling} alt='form_filling' className='img'/>
      </div>
    </div>
  );
};

export default PlanInput;
