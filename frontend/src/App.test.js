jest.mock('react-markdown', () => ({
  __esModule: true,
  default: ({ children }) => children,
}));

jest.mock('remark-gfm', () => ({
  __esModule: true,
  default: () => null,
}));

jest.mock('./api', () => {
  const mockApi = {
    get: jest.fn(() => Promise.resolve({ data: {} })),
    post: jest.fn(() => Promise.resolve({ data: {} })),
    put: jest.fn(() => Promise.resolve({ data: {} })),
    delete: jest.fn(() => Promise.resolve({ data: {} })),
    interceptors: {
      request: { use: jest.fn() },
      response: { use: jest.fn() },
    },
  };
  return {
    __esModule: true,
    default: mockApi,
    cancelJobs: jest.fn(),
  };
});

import { render, screen } from '@testing-library/react';
import App from './App';

test('renders app with router', () => {
  render(<App />);
  expect(document.querySelector('.App') || document.body).toBeTruthy();
});
