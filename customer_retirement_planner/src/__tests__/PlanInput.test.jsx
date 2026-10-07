import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import axios from 'axios';
import PlanInput from '../Components/Plan/PlanInput';

// Smoke test for the PlanInput submit flow. axios is mocked so no real HTTP
// request is made; we assert the form collects the 5 base fields, posts them,
// and renders the returned strategy via InvestmentResult.

jest.mock('axios');

const mockStrategy = {
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
      'Investment Percentage': 100,
      'Annual Return (%)': 10.1,
      'Risk Profile': 'Medium',
      'Future Value (INR)': 10234000,
    },
  ],
};

function fillAndSubmit() {
  fireEvent.change(screen.getByLabelText(/Current Age/i), { target: { value: '30' } });
  fireEvent.change(screen.getByLabelText(/Retirement Age/i), { target: { value: '60' } });
  fireEvent.change(screen.getByLabelText(/Desired Retirement Fund/i), {
    target: { value: '10000000' },
  });
  fireEvent.change(screen.getByLabelText(/Monthly Investment/i), {
    target: { value: '15000' },
  });
  // Risk is a custom radiogroup of buttons; click the Medium chip.
  fireEvent.click(screen.getByRole('radio', { name: /Medium/i }));
  fireEvent.click(screen.getByRole('button', { name: /Generate Strategy/i }));
}

afterEach(() => {
  jest.clearAllMocks();
});

test('renders the plan form with all base fields', () => {
  render(<PlanInput />);
  expect(screen.getByLabelText(/Current Age/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/Retirement Age/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/Desired Retirement Fund/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/Monthly Investment/i)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Generate Strategy/i })).toBeInTheDocument();
});

test('submits the 5 base fields and renders the returned strategy', async () => {
  axios.post.mockResolvedValueOnce({ data: mockStrategy });

  render(<PlanInput />);
  fillAndSubmit();

  // axios.post called once with the strategy endpoint and the collected fields.
  await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
  const [url, body] = axios.post.mock.calls[0];
  expect(url).toMatch(/\/investment-strategy$/);
  expect(body).toMatchObject({
    currentAge: '30',
    retirementAge: '60',
    desiredFund: '10000000',
    monthlyInvestment: '15000',
    riskCategory: 'medium',
  });

  // Results render.
  expect(await screen.findByText(/On track/i)).toBeInTheDocument();
  expect(screen.getAllByText(/NIFTYBEES\.NS/).length).toBeGreaterThan(0);
});

test('omits stepUpPercent from the payload when left blank (backward compatible)', async () => {
  axios.post.mockResolvedValueOnce({ data: mockStrategy });
  render(<PlanInput />);
  fillAndSubmit();
  await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
  const [, body] = axios.post.mock.calls[0];
  expect(body).not.toHaveProperty('stepUpPercent');
});

test('sends stepUpPercent as a number when provided', async () => {
  axios.post.mockResolvedValueOnce({ data: mockStrategy });
  render(<PlanInput />);
  fireEvent.change(screen.getByLabelText(/Current Age/i), { target: { value: '30' } });
  fireEvent.change(screen.getByLabelText(/Retirement Age/i), { target: { value: '60' } });
  fireEvent.change(screen.getByLabelText(/Desired Retirement Fund/i), { target: { value: '10000000' } });
  fireEvent.change(screen.getByLabelText(/Monthly Investment/i), { target: { value: '15000' } });
  fireEvent.change(screen.getByLabelText(/Yearly Step-Up/i), { target: { value: '10' } });
  fireEvent.click(screen.getByRole('radio', { name: /Medium/i }));
  fireEvent.click(screen.getByRole('button', { name: /Generate Strategy/i }));
  await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
  const [, body] = axios.post.mock.calls[0];
  expect(body.stepUpPercent).toBe(10);
});

test('shows a validation error and does not post when fields are empty', () => {
  render(<PlanInput />);
  fireEvent.click(screen.getByRole('button', { name: /Generate Strategy/i }));
  expect(screen.getByText(/Current age is required/i)).toBeInTheDocument();
  expect(axios.post).not.toHaveBeenCalled();
});

test('surfaces a server error message on request failure', async () => {
  axios.post.mockRejectedValueOnce({
    response: { data: { error: 'Failed to fetch stock market data. Please try again later.' } },
  });

  render(<PlanInput />);
  fillAndSubmit();

  expect(
    await screen.findByText(/Failed to fetch stock market data/i)
  ).toBeInTheDocument();
});
