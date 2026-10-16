import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import api from '../api';
import { FeaturesProvider, useFeatures, resetFeaturesCache } from './useFeatures';
import RequireBilling from '../RequireBilling';

jest.mock('../api', () => ({
  __esModule: true,
  default: { get: jest.fn() },
}));

const Probe = () => {
  const { edition, billing, loading } = useFeatures();
  return <div data-testid="probe">{loading ? 'loading' : `${edition}:${billing}`}</div>;
};

const renderGuarded = () =>
  render(
    <FeaturesProvider>
      <MemoryRouter initialEntries={['/plans']}>
        <Routes>
          <Route path="/plans" element={<RequireBilling><div>plans page</div></RequireBilling>} />
          <Route path="/chat" element={<div>chat page</div>} />
        </Routes>
      </MemoryRouter>
    </FeaturesProvider>
  );

beforeEach(() => {
  resetFeaturesCache();
  api.get.mockReset();
});

test('exposes community edition with billing off', async () => {
  api.get.mockResolvedValue({ data: { edition: 'community', billing: false } });
  render(<FeaturesProvider><Probe /></FeaturesProvider>);
  expect(screen.getByTestId('probe')).toHaveTextContent('loading');
  expect(await screen.findByText('community:false')).toBeInTheDocument();
  expect(api.get).toHaveBeenCalledWith('/features');
});

test('falls back to billing on when /features fails', async () => {
  api.get.mockRejectedValue(new Error('404'));
  jest.spyOn(console, 'warn').mockImplementation(() => {});
  render(<FeaturesProvider><Probe /></FeaturesProvider>);
  expect(await screen.findByText('cloud:true')).toBeInTheDocument();
  console.warn.mockRestore();
});

test('retries /features after a failed request', async () => {
  api.get
    .mockRejectedValueOnce(new Error('401'))
    .mockResolvedValueOnce({ data: { edition: 'community', billing: false } });
  jest.spyOn(console, 'warn').mockImplementation(() => {});
  const { unmount } = render(<FeaturesProvider><Probe /></FeaturesProvider>);
  expect(await screen.findByText('cloud:true')).toBeInTheDocument();
  unmount();
  render(<FeaturesProvider><Probe /></FeaturesProvider>);
  expect(await screen.findByText('community:false')).toBeInTheDocument();
  expect(api.get).toHaveBeenCalledTimes(2);
  console.warn.mockRestore();
});

test('fetches /features only once across providers', async () => {
  api.get.mockResolvedValue({ data: { edition: 'cloud', billing: true } });
  render(<FeaturesProvider><Probe /></FeaturesProvider>);
  render(<FeaturesProvider><Probe /></FeaturesProvider>);
  expect(await screen.findAllByText('cloud:true')).toHaveLength(2);
  expect(api.get).toHaveBeenCalledTimes(1);
});

test('useFeatures without a provider assumes billing on', () => {
  render(<Probe />);
  expect(screen.getByTestId('probe')).toHaveTextContent('cloud:true');
});

test('RequireBilling redirects to /chat when billing is off', async () => {
  api.get.mockResolvedValue({ data: { edition: 'community', billing: false } });
  renderGuarded();
  expect(await screen.findByText('chat page')).toBeInTheDocument();
  expect(screen.queryByText('plans page')).not.toBeInTheDocument();
});

test('RequireBilling renders the page when billing is on', async () => {
  api.get.mockResolvedValue({ data: { edition: 'cloud', billing: true } });
  renderGuarded();
  expect(await screen.findByText('plans page')).toBeInTheDocument();
});
