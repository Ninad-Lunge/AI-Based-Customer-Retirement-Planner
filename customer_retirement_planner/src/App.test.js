import { render } from '@testing-library/react';
import App from './App';

// Smoke test: the app renders its router + home route without crashing.
// (The previous CRA stub asserted a "learn react" link that no longer exists.)
test('renders without crashing', () => {
  const { container } = render(<App />);
  expect(container).toBeTruthy();
});
