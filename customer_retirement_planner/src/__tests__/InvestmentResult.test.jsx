import { render, screen } from '@testing-library/react';
import InvestmentResult from '../Components/Plan/InvestmentResult';

// Smoke tests for InvestmentResult: given a strategy object shaped exactly like
// the backend response, it should render the goal banner, key stats, allocation
// legend, and the breakdown table. No network is involved.

const baseFormData = {
  currentAge: '30',
  retirementAge: '60',
  desiredFund: '10000000',
  monthlyInvestment: '15000',
  riskCategory: 'medium',
};

const strategy = {
  'Total Future Value (INR)': 10234000,
  'Average Annual Return (%)': 11.2,
  'Value at Risk (5th percentile) (INR)': 7120000,
  'Optimistic Value (90th percentile) (INR)': 13990000,
  'Average Value from Simulation (INR)': 10180000,
  'Goal Achievable': true,
  'Investment Suggestions': [
    {
      'Stock Name': 'NIFTYBEES.NS',
      'Asset Class': 'Indian ETF / Mutual Fund',
      'Investment Percentage': 60,
      'Annual Return (%)': 10.1,
      'Risk Profile': 'Medium',
      'Future Value (INR)': 6000000,
    },
    {
      'Stock Name': 'GOLDBEES.NS',
      'Asset Class': 'Gold / Precious Metals',
      'Investment Percentage': 40,
      'Annual Return (%)': 8.5,
      'Risk Profile': 'Low',
      'Future Value (INR)': 4234000,
    },
  ],
};

test('renders nothing when no strategy is provided', () => {
  const { container } = render(
    <InvestmentResult formData={baseFormData} investmentStrategy={null} />
  );
  expect(container).toBeEmptyDOMElement();
});

test('renders the goal banner and stat cards for a successful strategy', () => {
  render(<InvestmentResult formData={baseFormData} investmentStrategy={strategy} />);

  // Goal banner (achieved).
  expect(screen.getByText(/On track/i)).toBeInTheDocument();

  // Key stat labels. "Projected Corpus" also appears in the banner sentence,
  // so match all occurrences and assert at least the stat-card label exists.
  expect(screen.getAllByText(/Projected Corpus/i).length).toBeGreaterThan(0);
  expect(screen.getByText(/Avg Annual Return/i)).toBeInTheDocument();
});

test('renders a breakdown row for every suggestion', () => {
  render(<InvestmentResult formData={baseFormData} investmentStrategy={strategy} />);

  // Each ticker appears (allocation legend + table both reference it; getAllByText).
  expect(screen.getAllByText(/NIFTYBEES\.NS/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/GOLDBEES\.NS/).length).toBeGreaterThan(0);

  // The breakdown table header is present.
  expect(screen.getByText(/Investment Breakdown/i)).toBeInTheDocument();
});

test('renders the error branch when the strategy carries an error', () => {
  render(
    <InvestmentResult
      formData={baseFormData}
      investmentStrategy={{ error: 'No stocks available in the selected risk category.' }}
    />
  );
  expect(screen.getByText(/Unable to generate a strategy/i)).toBeInTheDocument();
  expect(screen.getByText(/No stocks available/i)).toBeInTheDocument();
});
