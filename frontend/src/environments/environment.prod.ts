export const environment = {
  production: true,
  apiUrl: (typeof window !== 'undefined' && window.__env?.apiUrl) || 'http://127.0.0.1:8000',
  /** Where "Route this to the right team" emails go. */
  helpdeskEmail: 'HumanResource@triconinfotech.com'
};
