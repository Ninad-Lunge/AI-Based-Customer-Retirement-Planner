// ResultPage.js
import React from 'react';
import { useLocation } from 'react-router-dom';

const ResultPage = () => {
  const location = useLocation();
  const { formData } = location.state;

  return (
    <div>
      {/* Render the data received from PlanInput */}
      <h2>Result Page</h2>
      <p>Current Age: {formData.currentAge}</p>
      <p>Retirement Age: {formData.retirementAge}</p>
      {/* Display other formData fields */}
    </div>
  );
};

export default ResultPage;