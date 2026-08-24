import { useState, useEffect } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
  PieChart, Pie, Legend,
  LineChart, Line,
  AreaChart, Area,
} from 'recharts';
import {
  getDashboardStats,
  getDashboardStatusChart,
  getDashboardActionsChart,
  getDashboardDailyCases,
  getDashboardDailyRecovered,
} from '../services/api';

const STATUS_COLORS = {
  OPEN: '#3b82f6',
  IN_PROGRESS: '#f59e0b',
  RECOVERY_ATTEMPTED: '#8b5cf6',
  RECOVERED: '#10b981',
  FAILED: '#ef4444',
  ESCALATED: '#f97316',
  CLOSED: '#6b7280',
};

const ACTION_STATUS_COLORS = {
  SUCCESS: '#10b981',
  FAILED: '#ef4444',
  BLOCKED_BY_POLICY: '#f59e0b',
  PENDING: '#6b7280',
  ESCALATED: '#f97316',
};

function formatCurrency(amount) {
  if (amount === null || amount === undefined) return '₹0';
  return '₹' + Number(amount).toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

export default function Analytics() {
  const [stats, setStats] = useState(null);
  const [statusData, setStatusData] = useState([]);
  const [actionsData, setActionsData] = useState([]);
  const [dailyCases, setDailyCases] = useState([]);
  const [dailyRecovered, setDailyRecovered] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function fetchData() {
      try {
        const [statsRes, statusRes, actionsRes, casesRes, recoveredRes] = await Promise.all([
          getDashboardStats(),
          getDashboardStatusChart(),
          getDashboardActionsChart(),
          getDashboardDailyCases(30),
          getDashboardDailyRecovered(30),
        ]);
        setStats(statsRes.data);
        setStatusData(statusRes.data);
        setActionsData(actionsRes.data);
        setDailyCases(casesRes.data);
        setDailyRecovered(recoveredRes.data);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4">
        <p className="text-red-800 text-sm">Failed to load analytics: {error}</p>
      </div>
    );
  }

  // Prepare action data grouped by action_type
  const actionTypes = [...new Set(actionsData.map(d => d.action_type))];
  const actionSummary = actionTypes.map(actionType => {
    const rows = actionsData.filter(d => d.action_type === actionType);
    const entry = { action_type: actionType.replace(/_/g, ' ') };
    rows.forEach(r => { entry[r.execution_status] = r.count; });
    return entry;
  });

  // Prepare revenue at risk vs recovered
  const revenueComparison = [
    { name: 'Revenue at Risk', value: stats?.total_at_risk || 0 },
    { name: 'Revenue Recovered', value: stats?.total_recovered || 0 },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Analytics</h1>
        <p className="text-sm text-gray-500 mt-1">Revenue recovery metrics, trends, and performance</p>
      </div>

      {/* Revenue Comparison */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-semibold text-gray-700 mb-4">Revenue at Risk vs Recovered</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={revenueComparison}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="name" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`} />
              <Tooltip formatter={(v) => formatCurrency(v)} />
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                <Cell fill="#ef4444" />
                <Cell fill="#10b981" />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-semibold text-gray-700 mb-4">Recovery Rate</h3>
          <div className="flex items-center justify-center h-[250px]">
            <div className="text-center">
              <div className="relative inline-flex items-center justify-center">
                <svg className="w-40 h-40" viewBox="0 0 120 120">
                  <circle cx="60" cy="60" r="50" fill="none" stroke="#e5e7eb" strokeWidth="10" />
                  <circle
                    cx="60" cy="60" r="50" fill="none" stroke="#10b981" strokeWidth="10"
                    strokeDasharray={`${(stats?.recovery_rate || 0) * 3.14} 314`}
                    strokeDashoffset="78.5"
                    strokeLinecap="round"
                    transform="rotate(-90 60 60)"
                  />
                </svg>
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  <span className="text-3xl font-bold text-gray-900">{stats?.recovery_rate || 0}%</span>
                  <span className="text-xs text-gray-500">Recovery Rate</span>
                </div>
              </div>
              <div className="mt-4 grid grid-cols-2 gap-4 text-sm">
                <div>
                  <p className="text-gray-500">Recovered</p>
                  <p className="font-bold text-green-600">{stats?.recovered_count || 0}</p>
                </div>
                <div>
                  <p className="text-gray-500">Total Cases</p>
                  <p className="font-bold text-gray-900">{stats?.total_cases || 0}</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Cases Over Time */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h3 className="text-sm font-semibold text-gray-700 mb-4">Cases & Recovered Revenue Over Time</h3>
        {dailyCases.length > 0 ? (
          <ResponsiveContainer width="100%" height={300}>
            <AreaChart data={dailyCases.map((c, i) => ({
              ...c,
              recovered: dailyRecovered.find(r => r.date === c.date)?.amount || 0,
            }))}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
              <YAxis yAxisId="left" tick={{ fontSize: 11 }} />
              <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11 }} tickFormatter={(v) => `₹${v}`} />
              <Tooltip />
              <Legend />
              <Area yAxisId="left" type="monotone" dataKey="count" stroke="#3b82f6" fill="#dbeafe" name="Cases" />
              <Line yAxisId="right" type="monotone" dataKey="recovered" stroke="#10b981" strokeWidth={2} name="Recovered (₹)" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <p className="text-sm text-gray-400 text-center py-8">No data yet</p>
        )}
      </div>

      {/* Recovery by Status + Recovery by Action */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-semibold text-gray-700 mb-4">Recovery Outcomes by Status</h3>
          {statusData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie
                  data={statusData.map(d => ({ name: d.status, value: d.count }))}
                  cx="50%"
                  cy="50%"
                  innerRadius={50}
                  outerRadius={90}
                  paddingAngle={2}
                  dataKey="value"
                  label={({ name, percent }) => `${name.replace(/_/g, ' ')} (${(percent * 100).toFixed(0)}%)`}
                >
                  {statusData.map((d) => (
                    <Cell key={d.status} fill={STATUS_COLORS[d.status] || '#9ca3af'} />
                  ))}
                </Pie>
                <Tooltip />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-sm text-gray-400 text-center py-8">No data</p>
          )}
        </div>

        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-semibold text-gray-700 mb-4">Recovery by Action Type</h3>
          {actionSummary.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={actionSummary}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="action_type" tick={{ fontSize: 9 }} angle={-20} textAnchor="end" height={60} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Legend />
                <Bar dataKey="SUCCESS" stackId="a" fill="#10b981" name="Success" />
                <Bar dataKey="FAILED" stackId="a" fill="#ef4444" name="Failed" />
                <Bar dataKey="BLOCKED_BY_POLICY" stackId="a" fill="#f59e0b" name="Blocked" />
                <Bar dataKey="PENDING" stackId="a" fill="#d1d5db" name="Pending" />
                <Bar dataKey="ESCALATED" stackId="a" fill="#f97316" name="Escalated" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-sm text-gray-400 text-center py-8">No action data yet</p>
          )}
        </div>
      </div>

      {/* Escalation Rate */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h3 className="text-sm font-semibold text-gray-700 mb-4">Escalation & Policy Metrics</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <div className="text-center p-4 bg-gray-50 rounded-lg">
            <p className="text-2xl font-bold text-orange-600">{stats?.escalated_count || 0}</p>
            <p className="text-xs text-gray-500 mt-1">Escalated Cases</p>
          </div>
          <div className="text-center p-4 bg-gray-50 rounded-lg">
            <p className="text-2xl font-bold text-amber-600">{stats?.blocked_actions || 0}</p>
            <p className="text-xs text-gray-500 mt-1">Policy Blocked</p>
          </div>
          <div className="text-center p-4 bg-gray-50 rounded-lg">
            <p className="text-2xl font-bold text-blue-600">{stats?.awaiting_action || 0}</p>
            <p className="text-xs text-gray-500 mt-1">Awaiting Action</p>
          </div>
          <div className="text-center p-4 bg-gray-50 rounded-lg">
            <p className="text-2xl font-bold text-gray-900">{stats?.total_actions || 0}</p>
            <p className="text-xs text-gray-500 mt-1">Total Actions</p>
          </div>
        </div>
      </div>
    </div>
  );
}
