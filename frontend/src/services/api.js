import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Response interceptor for error handling
api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const message =
      error.response?.data?.detail ||
      error.response?.data?.error?.message ||
      error.message ||
      'An unexpected error occurred';
    return Promise.reject(new Error(message));
  }
);

// ============================================================================
// Health
// ============================================================================
export const healthCheck = () => api.get('/health');

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
