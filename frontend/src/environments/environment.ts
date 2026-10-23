export const environment = {
  production: false,
  apiUrl: (typeof window !== 'undefined' && window.__env?.apiUrl) || 'http://127.0.0.1:8000',
  useMock: false, // when true, the application will use mock data instead of making real API calls
};
