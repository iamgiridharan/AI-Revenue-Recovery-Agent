import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { getAuditEvents, getMLHealth } from '../services/api';

const EVENT_TYPE_COLORS = {
  CASE_CREATED: 'bg-blue-50 text-blue-700',
  CASE_UPDATED: 'bg-gray-50 text-gray-700',
  RISK_ASSESSED: 'bg-purple-50 text-purple-700',
  DIAGNOSIS_COMPLETED: 'bg-indigo-50 text-indigo-700',
  ACTION_RECOMMENDED: 'bg-cyan-50 text-cyan-700',
  POLICY_CHECKED: 'bg-amber-50 text-amber-700',
  ACTION_EXECUTED: 'bg-green-50 text-green-700',
  ACTION_FAILED: 'bg-red-50 text-red-700',
  CASE_ESCALATED: 'bg-orange-50 text-orange-700',
  CASE_CLOSED: 'bg-gray-50 text-gray-500',
};

const DECISION_COLORS = {
  APPROVED: 'text-green-600',
  BLOCKED: 'text-red-600',
  ESCALATED: 'text-orange-600',
  FALLBACK: 'text-gray-600',
};

function formatDate(isoString) {
  if (!isoString) return '-';
  return new Date(isoString).toLocaleString('en-IN', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

export default function AgentMonitor() {
  const [events, setEvents] = useState([]);
  const [pagination, setPagination] = useState({ page: 1, page_size: 30, total: 0, total_pages: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [mlStatus, setMlStatus] = useState(null);
  const [actorFilter, setActorFilter] = useState('');

  const fetchEvents = useCallback(async (page = 1) => {
    setLoading(true);
    setError(null);
    try {
      const params = { page, page_size: 30 };
      if (actorFilter) params.actor = actorFilter;
      const res = await getAuditEvents(params);
      setEvents(res.data);
      setPagination(res.pagination);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [actorFilter]);

  useEffect(() => {
    fetchEvents(1);
    getMLHealth().then(res => setMlStatus(res.data)).catch(() => {});
  }, [fetchEvents]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Agent Monitor</h1>
        <p className="text-sm text-gray-500 mt-1">AI agent activity, decisions, and audit trail</p>
      </div>

      {/* Status Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-xs text-gray-500 font-medium">ML Model</p>
          <div className="flex items-center gap-2 mt-1">
            <div className={`w-2 h-2 rounded-full ${mlStatus?.status === 'healthy' ? 'bg-green-500' : 'bg-gray-400'}`} />
            <span className="text-sm font-medium">{mlStatus?.status || 'Unknown'}</span>
          </div>
          {mlStatus?.model_version && (
            <p className="text-xs text-gray-400 mt-0.5">v{mlStatus.model_version}</p>
          )}
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-xs text-gray-500 font-medium">Total Audit Events</p>
          <p className="text-xl font-bold text-gray-900 mt-1">{pagination.total}</p>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <p className="text-xs text-gray-500 font-medium">Actor Filter</p>
          <select
            value={actorFilter}
            onChange={(e) => setActorFilter(e.target.value)}
            className="mt-1 border border-gray-300 rounded-lg px-2 py-1 text-sm w-full focus:ring-2 focus:ring-blue-500"
          >
            <option value="">All Actors</option>
            <option value="ai_agent">AI Agent</option>
            <option value="policy_engine">Policy Engine</option>
            <option value="recovery_service">Recovery Service</option>
            <option value="webhook">Webhook</option>
          </select>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <p className="text-red-800 text-sm">Error: {error}</p>
        </div>
      )}

      {/* Events Table */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
          </div>
        ) : events.length === 0 ? (
          <div className="text-center py-12">
            <svg className="w-12 h-12 text-gray-300 mx-auto mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
            </svg>
            <p className="text-gray-500 font-medium">No audit events yet</p>
            <p className="text-sm text-gray-400 mt-1">Events will appear as the agent processes cases</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Time</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Event</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Actor</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Case</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Decision</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Action</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Result</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {events.map((e) => (
                  <tr key={e.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">{formatDate(e.timestamp)}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${EVENT_TYPE_COLORS[e.event_type] || 'bg-gray-50 text-gray-600'}`}>
                        {e.event_type?.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-600">{e.actor}</td>
                    <td className="px-4 py-3">
                      <Link to={`/cases/${e.case_id}`} className="text-blue-600 hover:text-blue-800 text-xs font-mono">
                        {e.case_id}
                      </Link>
                    </td>
                    <td className={`px-4 py-3 text-xs font-medium ${DECISION_COLORS[e.decision] || 'text-gray-600'}`}>
                      {e.decision || '-'}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-600">
                      {e.action?.replace(/_/g, ' ') || '-'}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-600">{e.result || '-'}</td>
                    <td className="px-4 py-3">
                      {e.reason && (
                        <p className="text-xs text-gray-400 max-w-xs truncate" title={e.reason}>{e.reason}</p>
                      )}
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
            Page {pagination.page} of {pagination.total_pages} ({pagination.total} events)
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => fetchEvents(pagination.page - 1)}
              disabled={pagination.page <= 1}
              className="px-3 py-1.5 text-sm border border-gray-300 rounded-lg disabled:opacity-40 hover:bg-gray-50"
            >
              Previous
            </button>
            <button
              onClick={() => fetchEvents(pagination.page + 1)}
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
