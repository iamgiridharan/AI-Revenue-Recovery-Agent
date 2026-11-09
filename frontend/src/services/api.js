import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Shared demonstration key for the mutating endpoints (diagnose, recovery, review,
// policy, simulation). IMPORTANT: Vite bakes VITE_* values into the browser
// bundle, so this is NOT a secret -- anyone can read it from the page source.
// It is a local academic-demo convenience only. A public deployment must
// terminate authentication at the backend or a reverse proxy.
const demoApiKey = import.meta.env.VITE_DEMO_API_KEY;
if (demoApiKey) {
  api.defaults.headers.common['X-API-Key'] = demoApiKey;
}

// Turn a FastAPI error payload into a message a human can act on.
// Validation failures arrive as `detail: [{ loc, msg, type }]`; passing that
// array to `new Error()` produced the useless "[object Object]" banner.
export function formatErrorMessage(error) {
  const data = error.response?.data;
  const detail = data?.detail ?? data?.error?.message;

  if (typeof detail === 'string' && detail.trim()) return detail;

  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === 'string') return item;
        if (!item || typeof item !== 'object') return null;
        const msg = item.msg || item.message;
        if (!msg) return null;
        const field = Array.isArray(item.loc)
          ? item.loc.filter((part) => part !== 'body' && part !== 'query').join('.')
          : '';
        // Pydantic prefixes custom validator messages with "Value error, ".
        const clean = String(msg).replace(/^Value error, /, '');
        return field ? `${field}: ${clean}` : clean;
      })
      .filter(Boolean);

    if (messages.length) return messages.join('; ');
  }

  if (detail && typeof detail === 'object') {
    return detail.msg || detail.message || JSON.stringify(detail);
  }

  return error.message || 'An unexpected error occurred';
}

// Authentication failures are the one error class this browser cannot fix on its
// own: the server rejects the request before it reaches any business logic, and the
// remedy is always a configuration value, not a retry. The backend's message says
// what it saw (a missing header, a wrong key); append where that value comes from so
// the banner tells the reader what to change. Only the variable names are added --
// never the key values themselves.
function authHint(status) {
  if (status === 503) {
    return 'Restart the API after setting it.';
  }
  if (status === 401) {
    return demoApiKey
      ? "VITE_DEMO_API_KEY in frontend/.env.local does not match this server's DEMO_API_KEY; correct it and restart the dev server."
      : 'This frontend was built without VITE_DEMO_API_KEY; set it in frontend/.env.local to match the server\'s DEMO_API_KEY and restart the dev server.';
  }
  return '';
}

function formatWithAuthContext(error) {
  const message = formatErrorMessage(error);
  const hint = authHint(error.response?.status);
  return hint ? `${message} ${hint}` : message;
}

// Response interceptor for error handling
api.interceptors.response.use(
  (response) => response.data,
  (error) => Promise.reject(new Error(formatWithAuthContext(error)))
);

// ============================================================================
// Health
// ============================================================================
export const healthCheck = () => api.get('/health');
// Presence flags + mode label only: never returns Razorpay keys or secrets.
export const getConfigStatus = () => api.get('/config/status');

// ============================================================================
// Dashboard
// ============================================================================
export const getDashboardStats = () => api.get('/dashboard/stats');
export const getDashboardStatusChart = () => api.get('/dashboard/charts/status');
export const getDashboardPriorityChart = () => api.get('/dashboard/charts/priority');
export const getDashboardActionsChart = () => api.get('/dashboard/charts/actions');
export const getDashboardDailyCases = (days = 30) =>
  api.get(`/dashboard/charts/daily-cases?days=${days}`);
export const getDashboardDailyRecovered = (days = 30) =>
  api.get(`/dashboard/charts/daily-recovered?days=${days}`);

// ============================================================================
// Cases
// ============================================================================
export const getCases = (params = {}) => {
  const query = new URLSearchParams();
  if (params.page) query.set('page', params.page);
  if (params.page_size) query.set('page_size', params.page_size);
  if (params.status) query.set('status', params.status);
  if (params.priority) query.set('priority', params.priority);
  if (params.min_risk_score !== undefined) query.set('min_risk_score', params.min_risk_score);
  if (params.max_risk_score !== undefined) query.set('max_risk_score', params.max_risk_score);
  const qs = query.toString();
  return api.get(`/cases${qs ? '?' + qs : ''}`);
};

export const getCaseDetail = (caseId) => api.get(`/cases/${caseId}`);
export const getCaseFullDetail = (caseId) => api.get(`/cases/${caseId}/detail`);

// Execute the case's recorded AI recommendation. The backend authorizes it with
// the Policy Engine and creates a Razorpay Test Mode payment link (or a clearly
// labelled mock when no credentials are configured).
export const executeRecovery = (caseId, data = {}) =>
  api.post(`/cases/${caseId}/recovery`, data);

// ============================================================================
// Audit
// ============================================================================
export const getAuditEvents = (params = {}) => {
  const query = new URLSearchParams();
  if (params.page) query.set('page', params.page);
  if (params.page_size) query.set('page_size', params.page_size);
  if (params.case_id) query.set('case_id', params.case_id);
  if (params.event_type) query.set('event_type', params.event_type);
  if (params.actor) query.set('actor', params.actor);
  const qs = query.toString();
  return api.get(`/audit${qs ? '?' + qs : ''}`);
};

// ============================================================================
// Agent
// ============================================================================
export const diagnoseCase = (caseId) =>
  api.post('/agent/diagnose', { case_id: caseId });

// ============================================================================
// Human review
// ============================================================================
// An ESCALATED case is one the Policy Engine refused to authorize on its own, so
// it waits for a named human. The queue is read-only; the review endpoint records
// the decision (approve, override, close) against that reviewer.
export const getReviewQueue = (limit = 100) =>
  api.get(`/reviews/queue?limit=${limit}`);

export const reviewCase = (caseId, data) =>
  api.post(`/cases/${caseId}/review`, data);

// ============================================================================
// Simulation
// ============================================================================
// A simulation writes synthetic transactions, cases, recovery actions and audit
// events into the same database, and its results are labelled SIMULATED. Large
// batches take far longer than the default request timeout.
export const runSimulation = (data = {}) =>
  api.post('/simulation/run', data, { timeout: 300000 });

// ============================================================================
// ML
// ============================================================================
export const getMLModelInfo = () => api.get('/ml/model');
export const getMLHealth = () => api.get('/ml/health');

// ============================================================================
// Policy
// ============================================================================
export const getPolicyConfig = () => api.get('/policies');
export const updatePolicyConfig = (data) => api.put('/policies', data);
export const evaluatePolicyAction = (params) => {
  const query = new URLSearchParams({
    case_id: params.case_id,
    proposed_action: params.proposed_action,
    confidence: params.confidence,
    recovery_probability: params.recovery_probability,
  });
  return api.post(`/policies/evaluate?${query.toString()}`);
};

export default api;
