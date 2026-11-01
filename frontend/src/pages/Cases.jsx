import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { getCases, runSimulation, getReviewQueue } from '../services/api';

const STATUS_OPTIONS = ['OPEN', 'IN_PROGRESS', 'RECOVERY_ATTEMPTED', 'RECOVERED', 'FAILED', 'ESCALATED', 'CLOSED'];
const PRIORITY_OPTIONS = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];

const STATUS_STYLES = {
  OPEN: 'bg-blue-50 text-blue-700 border-blue-200',
  IN_PROGRESS: 'bg-amber-50 text-amber-700 border-amber-200',
  RECOVERY_ATTEMPTED: 'bg-purple-50 text-purple-700 border-purple-200',
  RECOVERED: 'bg-green-50 text-green-700 border-green-200',
  FAILED: 'bg-red-50 text-red-700 border-red-200',
  ESCALATED: 'bg-orange-50 text-orange-700 border-orange-200',
  CLOSED: 'bg-gray-50 text-gray-700 border-gray-200',
};

const PRIORITY_STYLES = {
  LOW: 'bg-gray-100 text-gray-600',
  MEDIUM: 'bg-blue-50 text-blue-700',
  HIGH: 'bg-amber-50 text-amber-700',
  CRITICAL: 'bg-red-50 text-red-700',
};

function formatCurrency(amount) {
  if (amount === null || amount === undefined) return '-';
  return '₹' + Number(amount).toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function formatDate(isoString) {
  if (!isoString) return '-';
  const d = new Date(isoString);
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function RiskBar({ value }) {
  if (value === null || value === undefined) return <span className="text-gray-400">-</span>;
  const pct = Math.round(value * 100);
  const color = pct >= 80 ? 'bg-red-500' : pct >= 60 ? 'bg-amber-500' : pct >= 40 ? 'bg-yellow-400' : 'bg-green-500';
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 bg-gray-200 rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-gray-600">{pct}%</span>
    </div>
  );
}

/**
 * Run a batch simulation of synthetic payment failures.
 *
 * The simulation pushes synthetic transactions through the full pipeline
 * (ML → agent → policy → simulated recovery) and labels everything SIMULATED.
 * It writes those synthetic cases, actions and audit events into the same
 * database, so the list is refreshed afterwards.
 */
function SimulationPanel({ onSimulated }) {
  const [open, setOpen] = useState(false);
  const [numTransactions, setNumTransactions] = useState(25);
  const [seed, setSeed] = useState('');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  async function handleRun(event) {
    event.preventDefault();
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const payload = { num_transactions: Number(numTransactions) };
      if (seed !== '') payload.seed = Number(seed);
      const res = await runSimulation(payload);
      setResult(res);
      if (onSimulated) onSimulated();
    } catch (err) {
      setError(err.message);
    } finally {
      setRunning(false);
    }
  }

  const metrics = result?.metrics;

  return (
    <div className="bg-white rounded-xl border border-gray-200">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="w-full flex items-center justify-between px-4 py-3 text-left"
      >
        <span>
          <span className="text-sm font-semibold text-gray-800">Simulation</span>
          <span className="text-xs text-gray-500 ml-2">
            Generate synthetic failed payments and run them through the full pipeline
          </span>
        </span>
        <span className="text-xs text-blue-600">{open ? 'Hide' : 'Run a simulation'}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 border-t border-gray-100 pt-4 space-y-3">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold tracking-wide px-2 py-0.5 rounded bg-purple-50 text-purple-700 border border-purple-200">
              SIMULATED
            </span>
            <span className="text-xs text-gray-500">
              No payment provider is called, and these cases are written into the same
              database as real ones.
            </span>
          </div>

          <form onSubmit={handleRun} className="flex flex-wrap items-end gap-3">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">
                Transactions (10–50000)
              </label>
              <input
                type="number"
                min={10}
                max={50000}
                value={numTransactions}
                onChange={(e) => setNumTransactions(e.target.value)}
                className="w-32 border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">
                Seed (optional)
              </label>
              <input
                type="number"
                value={seed}
                onChange={(e) => setSeed(e.target.value)}
                placeholder="random"
                className="w-32 border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
              />
            </div>
            <button
              type="submit"
              disabled={running}
              className="px-4 py-2 text-sm bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-40 flex items-center gap-2"
            >
              {running && <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />}
              {running ? 'Simulating…' : 'Run simulation'}
            </button>
          </form>

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3">
              <p className="text-xs font-semibold text-red-700 mb-0.5">Simulation failed</p>
              <p className="text-sm text-red-800">{error}</p>
            </div>
          )}

          {result && (
            <div className="border border-gray-200 rounded-lg p-3 space-y-3">
              <p className="text-xs text-gray-500">
                {result.simulation_id} · {result.num_transactions_processed} transactions ·{' '}
                {Number(result.processing_duration_seconds || 0).toFixed(2)}s
              </p>

              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
                {[
                  { label: 'Recoverable', value: result.recoverable_cases, color: 'text-blue-600' },
                  { label: 'Recovered', value: result.successful_recoveries, color: 'text-green-600' },
                  { label: 'Failed', value: result.failed_recoveries, color: 'text-red-600' },
                  { label: 'Escalated', value: result.escalations, color: 'text-orange-600' },
                  { label: 'Policy blocked', value: result.policy_blocks, color: 'text-amber-600' },
                  { label: 'Recovery rate', value: `${Math.round((result.recovery_rate || 0) * 100)}%`, color: 'text-gray-900' },
                ].map((tile) => (
                  <div key={tile.label} className="bg-gray-50 rounded-lg p-2 text-center">
                    <p className={`text-lg font-semibold ${tile.color}`}>{tile.value ?? 0}</p>
                    <p className="text-[10px] text-gray-500">{tile.label}</p>
                  </div>
                ))}
              </div>

              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <p className="text-xs text-gray-500">Value at risk (simulated)</p>
                  <p className="font-medium text-gray-900">{formatCurrency(result.revenue_at_risk)}</p>
                </div>
                <div>
                  <p className="text-xs text-gray-500">Recovered (simulated)</p>
                  <p className="font-medium text-green-700">
                    {formatCurrency(result.simulated_revenue_recovered)}
                  </p>
                </div>
              </div>

              {metrics && (
                <p className="text-[11px] text-gray-400">
                  {metrics.total_recovery_attempts} recovery attempts ·{' '}
                  {metrics.outstanding_revenue} outstanding ·{' '}
                  {result.total_policy_decisions} policy decisions ·{' '}
                  {result.total_audit_events} audit events
                </p>
              )}

              {Object.keys(result.recovery_action_breakdown || {}).length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {Object.entries(result.recovery_action_breakdown).map(([action, count]) => (
                    <span
                      key={action}
                      className="text-[11px] px-2 py-0.5 rounded bg-gray-50 border border-gray-200 text-gray-600"
                    >
                      {action.replace(/_/g, ' ')}: {count}
                    </span>
                  ))}
                </div>
              )}

              <p className="text-[11px] text-gray-400">
                Simulated figures are not booked revenue. Only a verified payment
                webhook settles a case and books a recovered amount.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Escalated cases awaiting a named human.
 *
 * Read-only: it lists the cases the Policy Engine refused to authorize on its own
 * together with the rule that escalated each one. The decision itself is made on
 * the case page, where the reviewer can see the full history.
 */
function ReviewQueue({ refreshSignal }) {
  const [queue, setQueue] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getReviewQueue()
      .then((res) => {
        if (cancelled) return;
        setQueue(res.data);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [refreshSignal]);

  const queueCases = queue?.cases || [];
  const total = queue?.total ?? 0;

  return (
    <div className="bg-white rounded-xl border border-orange-200">
      <div className="px-4 py-3 border-b border-orange-100 flex flex-wrap items-center justify-between gap-2">
        <div>
          <span className="text-sm font-semibold text-gray-800">Human review queue</span>
          <span className="text-xs text-gray-500 ml-2">
            {loading
              ? 'Loading…'
              : `${total} escalated case${total === 1 ? '' : 's'} · ${formatCurrency(queue?.total_amount_at_risk)} at risk`}
          </span>
        </div>
        {!loading && total > 0 && (
          <span className="text-[10px] font-bold tracking-wide px-2 py-0.5 rounded bg-orange-50 text-orange-700 border border-orange-200">
            AWAITING REVIEW
          </span>
        )}
      </div>

      <div className="px-4 py-3">
        {error && (
          <p className="text-sm text-red-700">Could not load the review queue: {error}</p>
        )}

        {!error && !loading && total === 0 && (
          <p className="text-sm text-gray-500">
            Nothing is waiting for a human. Cases land here when the Policy Engine
            escalates instead of acting on its own: an amount above the autonomous
            limit, confidence or recovery probability below the configured minimum,
            or an explicit escalation by the agent.
          </p>
        )}

        {queueCases.length > 0 && (
          <div className="space-y-2">
            {queueCases.map((item) => (
              <div
                key={item.case_id}
                className="flex flex-wrap items-center gap-3 border border-gray-100 rounded-lg px-3 py-2"
              >
                <Link
                  to={`/cases/${item.case_id}`}
                  className="font-mono text-xs text-blue-600 hover:text-blue-800"
                >
                  {item.case_id}
                </Link>
                <span className="text-sm font-medium text-gray-900">{formatCurrency(item.amount)}</span>
                <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${PRIORITY_STYLES[item.priority] || ''}`}>
                  {item.priority}
                </span>
                <span className="text-xs text-gray-600">
                  {item.escalation?.rule ? (
                    <>
                      <span className="font-medium">{item.escalation.rule}</span>
                      {' — '}{item.escalation.rule_reason || item.escalation.reason}
                    </>
                  ) : (
                    item.escalation?.reason || 'Escalated'
                  )}
                </span>
                <span className="text-xs text-gray-400">
                  AI pick: {item.recommended_action ? item.recommended_action.replace(/_/g, ' ') : 'none'}
                </span>
                <Link
                  to={`/cases/${item.case_id}`}
                  className="ml-auto text-xs font-medium text-orange-700 hover:text-orange-900"
                >
                  Review
                </Link>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Cases() {
  const [refreshSignal, setRefreshSignal] = useState(0);
  const [cases, setCases] = useState([]);
  const [pagination, setPagination] = useState({ page: 1, page_size: 20, total: 0, total_pages: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filters, setFilters] = useState({
    status: '',
    priority: '',
  });

  const fetchCases = useCallback(async (page = 1) => {
    setLoading(true);
    setError(null);
    try {
      const params = { page, page_size: 20 };
      if (filters.status) params.status = filters.status;
      if (filters.priority) params.priority = filters.priority;
      const res = await getCases(params);
      setCases(res.data);
      setPagination(res.pagination);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    fetchCases(1);
  }, [fetchCases]);

  const handleFilterChange = (key, value) => {
    setFilters(prev => ({ ...prev, [key]: value }));
  };

  const clearFilters = () => {
    setFilters({ status: '', priority: '' });
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Revenue Risk Cases</h1>
        <p className="text-sm text-gray-500 mt-1">View and manage identified payment failures and their recovery status</p>
      </div>

      <SimulationPanel
        onSimulated={() => {
          fetchCases(1);
          setRefreshSignal((n) => n + 1);
        }}
      />

      <ReviewQueue refreshSignal={refreshSignal} />

      {/* Filters */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Status</label>
            <select
              value={filters.status}
              onChange={(e) => handleFilterChange('status', e.target.value)}
              className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            >
              <option value="">All Statuses</option>
              {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Priority</label>
            <select
              value={filters.priority}
              onChange={(e) => handleFilterChange('priority', e.target.value)}
              className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            >
              <option value="">All Priorities</option>
              {PRIORITY_OPTIONS.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
          <button
            onClick={clearFilters}
            className="text-sm text-gray-500 hover:text-gray-700 px-3 py-1.5"
          >
            Clear filters
          </button>
          <div className="ml-auto text-sm text-gray-500">
            {pagination.total} case{pagination.total !== 1 ? 's' : ''}
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <p className="text-red-800 text-sm">Error loading cases: {error}</p>
        </div>
      )}

      {/* Table */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
          </div>
        ) : cases.length === 0 ? (
          <div className="text-center py-12">
            <svg className="w-12 h-12 text-gray-300 mx-auto mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
            </svg>
            <p className="text-gray-500 font-medium">No cases found</p>
            <p className="text-sm text-gray-400 mt-1">
              {filters.status || filters.priority ? 'Try adjusting your filters' : 'Cases will appear once payment failures are detected'}
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Case ID</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Amount</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Risk</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Recovery Prob.</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Priority</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Status</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Action</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Recovered</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Created</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {cases.map((c) => (
                  <tr key={c.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-3">
                      <span className="font-mono text-xs text-blue-600">{c.case_id}</span>
                    </td>
                    <td className="px-4 py-3 font-medium text-gray-900">{formatCurrency(c.amount)}</td>
                    <td className="px-4 py-3"><RiskBar value={c.risk_score} /></td>
                    <td className="px-4 py-3">
                      {c.recovery_probability !== null ? (
                        <span className="text-sm">{Math.round(c.recovery_probability * 100)}%</span>
                      ) : (
                        <span className="text-gray-400">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${PRIORITY_STYLES[c.priority] || ''}`}>
                        {c.priority}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-block px-2 py-0.5 rounded border text-xs font-medium ${STATUS_STYLES[c.status] || ''}`}>
                        {c.status?.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-600">
                      {c.recommended_action ? c.recommended_action.replace(/_/g, ' ') : '-'}
                    </td>
                    <td className="px-4 py-3 font-medium text-green-600">
                      {c.recovered_amount > 0 ? formatCurrency(c.recovered_amount) : '-'}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-500">{formatDate(c.created_at)}</td>
                    <td className="px-4 py-3">
                      <Link
                        to={`/cases/${c.case_id}`}
                        className="text-blue-600 hover:text-blue-800 text-xs font-medium"
                      >
                        View
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Pagination */}
      {pagination.total_pages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-gray-500">
            Page {pagination.page} of {pagination.total_pages}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => fetchCases(pagination.page - 1)}
              disabled={pagination.page <= 1}
              className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg disabled:opacity-40 hover:bg-gray-50"
            >
              Previous
            </button>
            <button
              onClick={() => fetchCases(pagination.page + 1)}
              disabled={pagination.page >= pagination.total_pages}
              className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg disabled:opacity-40 hover:bg-gray-50"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
