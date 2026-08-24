import { useState, useEffect } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
  LineChart, Line,
} from 'recharts';
import {
  getDashboardStats,
  getDashboardStatusChart,
  getDashboardPriorityChart,
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

const PRIORITY_COLORS = {
  LOW: '#6b7280',
  MEDIUM: '#3b82f6',
  HIGH: '#f59e0b',
  CRITICAL: '#ef4444',
};

function StatCard({ title, value, subtitle, color = 'text-gray-900', icon }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm font-medium text-gray-500">{title}</p>
          <p className={`text-2xl font-bold mt-1 ${color}`}>{value}</p>
          {subtitle && <p className="text-xs text-gray-400 mt-1">{subtitle}</p>}
        </div>
        {icon && <div className="text-gray-300">{icon}</div>}
      </div>
    </div>
  );
}

function formatCurrency(amount) {
  if (amount === null || amount === undefined) return '₹0';
  return '₹' + Number(amount).toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [statusData, setStatusData] = useState([]);
  const [priorityData, setPriorityData] = useState([]);
  const [dailyCases, setDailyCases] = useState([]);
  const [dailyRecovered, setDailyRecovered] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function fetchData() {
      try {
        const [statsRes, statusRes, priorityRes, casesRes, recoveredRes] = await Promise.all([
          getDashboardStats(),
          getDashboardStatusChart(),
          getDashboardPriorityChart(),
          getDashboardDailyCases(30),
          getDashboardDailyRecovered(30),
        ]);
        setStats(statsRes.data);
        setStatusData(statusRes.data);
        setPriorityData(priorityRes.data);
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
        <p className="text-red-800 text-sm">Failed to load dashboard: {error}</p>
      </div>
    );
  }

  if (!stats) {
    return (
      <div className="text-center py-12">
        <p className="text-gray-500">No data available yet.</p>
        <p className="text-xs text-gray-400 mt-1">Cases will appear once payment failure detection is running.</p>
      </div>
    );
  }

  const pieData = statusData.map(d => ({ name: d.status, value: d.count }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Overview</h1>
        <p className="text-sm text-gray-500 mt-1">Revenue recovery metrics and system status</p>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Total Cases"
          value={stats.total_cases}
          subtitle={`${stats.total_customers} customers affected`}
        />
        <StatCard
          title="Revenue at Risk"
          value={formatCurrency(stats.total_at_risk)}
          subtitle={`${stats.awaiting_action} awaiting action`}
          color="text-red-600"
        />
        <StatCard
          title="Revenue Recovered"
          value={formatCurrency(stats.total_recovered)}
          subtitle={`${stats.recovery_rate}% recovery rate`}
          color="text-green-600"
        />
        <StatCard
          title="Recovery Rate"
          value={`${stats.recovery_rate}%`}
          subtitle={`${stats.recovered_count} of ${stats.total_cases} cases`}
          color="text-blue-600"
        />
      </div>

      {/* Secondary KPIs */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Recovery Attempts"
          value={stats.total_actions}
          subtitle={`${stats.successful_actions} successful`}
        />
        <StatCard
          title="Successful Recoveries"
          value={stats.successful_actions}
          subtitle="Actions completed"
          color="text-green-600"
        />
        <StatCard
          title="Failed Recoveries"
          value={stats.failed_actions}
          subtitle="Actions that failed"
          color="text-red-600"
        />
        <StatCard
          title="Escalated Cases"
          value={stats.escalated_count}
          subtitle="Needs human review"
          color="text-orange-600"
        />
      </div>

      {/* Charts Row 1 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Daily Cases & Recovered */}
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-semibold text-gray-700 mb-4">Daily Cases (Last 30 Days)</h3>
          {dailyCases.length > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={dailyCases}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="count" fill="#3b82f6" radius={[4, 4, 0, 0]} name="Cases" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-sm text-gray-400 text-center py-8">No case data yet</p>
          )}
        </div>

        {/* Revenue Recovered Over Time */}
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-semibold text-gray-700 mb-4">Revenue Recovered (Last 30 Days)</h3>
          {dailyRecovered.length > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={dailyRecovered}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `₹${v}`} />
                <Tooltip formatter={(v) => formatCurrency(v)} />
                <Line type="monotone" dataKey="amount" stroke="#10b981" strokeWidth={2} name="Recovered" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-sm text-gray-400 text-center py-8">No recovery data yet</p>
          )}
        </div>
      </div>

      {/* Charts Row 2 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Status Breakdown Pie */}
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-semibold text-gray-700 mb-4">Cases by Status</h3>
          {pieData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie
                  data={pieData}
                  cx="50%"
                  cy="50%"
                  innerRadius={60}
                  outerRadius={100}
                  paddingAngle={2}
                  dataKey="value"
                  label={({ name, percent }) => `${name} (${(percent * 100).toFixed(0)}%)`}
                >
                  {pieData.map((entry) => (
                    <Cell key={entry.name} fill={STATUS_COLORS[entry.name] || '#9ca3af'} />
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

        {/* Priority Breakdown */}
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-semibold text-gray-700 mb-4">Cases by Priority</h3>
          {priorityData.length > 0 ? (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={priorityData} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                <XAxis type="number" tick={{ fontSize: 11 }} />
                <YAxis type="category" dataKey="priority" tick={{ fontSize: 12 }} width={80} />
                <Tooltip />
                <Bar dataKey="count" name="Cases" radius={[0, 4, 4, 0]}>
                  {priorityData.map((entry) => (
                    <Cell key={entry.priority} fill={PRIORITY_COLORS[entry.priority] || '#9ca3af'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-sm text-gray-400 text-center py-8">No data</p>
          )}
        </div>
      </div>
    </div>
  );
}
