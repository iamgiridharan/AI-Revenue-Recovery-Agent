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
  getConfigStatus,
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

/** Signed change versus the previous 7 days. Null means "no previous period". */
function Delta({ amount, pct, invert = false }) {
  if (pct === null || pct === undefined) {
    return <span className="text-xs text-gray-400">no previous period</span>;
  }
  const rising = amount > 0;
  const good = invert ? !rising : rising;
  const colour = amount === 0 ? 'text-gray-500' : good ? 'text-green-600' : 'text-red-600';
  return (
    <span className={`text-xs font-medium ${colour}`}>
      {rising ? '+' : ''}{formatCurrency(amount)} ({rising ? '+' : ''}{pct}%)
    </span>
  );
}

/**
 * Where the recovered money came from. Simulated rupees and webhook-verified
 * rupees are never added together into a single unlabelled figure.
 */
function provenanceSummary(provenance) {
  if (!provenance) return '';
  const parts = [];
  if (provenance.verified?.amount > 0) parts.push(`${formatCurrency(provenance.verified.amount)} webhook-verified`);
  if (provenance.simulated?.amount > 0) parts.push(`${formatCurrency(provenance.simulated.amount)} simulated`);
  if (provenance.unattributed?.amount > 0) parts.push(`${formatCurrency(provenance.unattributed.amount)} unattributed`);
  return parts.join(' \u00b7 ');
}

/**
 * Says plainly whether executed recovery actions reach Razorpay or are mocked.
 * Without keys every executed action returns a mock.invalid link that cannot be
 * paid, so the dashboard must not imply that money can move.
 */
function PaymentModeBanner({ config }) {
  if (!config || !config.payments) return null;
  const payments = config.payments;
  const isMock = payments.mode !== 'TEST_MODE';

  return (
    <div
      className={`rounded-xl border p-4 ${isMock ? 'bg-amber-50 border-amber-200' : 'bg-blue-50 border-blue-200'}`}
    >
      <div className="flex items-start gap-3">
        <span
          className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold tracking-wide ${
            isMock ? 'bg-amber-200 text-amber-900' : 'bg-blue-200 text-blue-900'
          }`}
        >
          {isMock ? 'MOCK' : 'TEST MODE'}
        </span>
        <div className="min-w-0">
          <p className={`text-sm font-semibold ${isMock ? 'text-amber-900' : 'text-blue-900'}`}>
            {isMock
              ? 'MOCK — no provider call'
              : 'TEST MODE — Razorpay test keys loaded'}
          </p>
          <p className={`text-xs mt-0.5 ${isMock ? 'text-amber-800' : 'text-blue-800'}`}>
            {isMock
              ? 'Razorpay keys are not configured. Executing a recovery action creates a mock.invalid link that cannot be paid, so no customer can settle and no revenue can be booked as recovered.'
              : 'Executing a recovery action creates a real Razorpay test-mode link. Revenue is booked only when a signature-verified webhook confirms the payment.'}
          </p>
          {isMock ? (
            <p className="text-[11px] text-amber-700 mt-1">
              Add RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET to backend/.env to switch to Test Mode.
            </p>
          ) : (
            <div className="text-[11px] mt-1 space-y-0.5">
              {!payments.webhook_configured && (
                <p className="text-red-700">
                  Webhook secret is not configured: every webhook is rejected with 401, so no payment can settle.
                </p>
              )}
              {payments.key_type === 'live' && (
                <p className="text-red-700">
                  Live keys detected — this build is intended for Test Mode only.
                </p>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [statusData, setStatusData] = useState([]);
  const [priorityData, setPriorityData] = useState([]);
  const [dailyCases, setDailyCases] = useState([]);
  const [dailyRecovered, setDailyRecovered] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [config, setConfig] = useState(null);

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

  // Non-blocking: if the status endpoint is unavailable the dashboard still loads,
  // it just cannot state the payment mode.
  useEffect(() => {
    getConfigStatus()
      .then((res) => setConfig(res.data))
      .catch(() => setConfig(null));
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

      <PaymentModeBanner config={config} />

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Total Cases"
          value={stats.total_cases}
          subtitle={`${stats.total_customers} customers affected \u00b7 ${stats.awaiting_action} awaiting action`}
        />
        <StatCard
          title="Outstanding"
          value={formatCurrency(stats.outstanding_amount)}
          subtitle={`of ${formatCurrency(stats.total_case_value)} case value`}
          color="text-red-600"
        />
        <StatCard
          title="Money Recovered"
          value={formatCurrency(stats.total_recovered)}
          subtitle={provenanceSummary(stats.provenance) || 'nothing recovered yet'}
          color="text-green-600"
        />
        <StatCard
          title="Money Recovery Rate"
          value={`${stats.money_recovery_rate ?? 0}%`}
          subtitle={`${stats.recovery_rate}% of cases settled (${stats.recovered_count} of ${stats.total_cases})`}
          color="text-blue-600"
        />
      </div>

      {/* Last 7 days, with the change against the previous 7 days */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <div className="flex items-baseline justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-700">Last 7 Days</h3>
          <span className="text-xs text-gray-400">compared with the previous 7 days</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="bg-gray-50 rounded-lg p-4">
            <p className="text-xs font-medium text-gray-500">Recovered</p>
            <p className="text-xl font-bold text-green-600 mt-1">{formatCurrency(stats.recovered_7d)}</p>
            <div className="mt-1">
              <Delta amount={stats.recovered_delta_7d} pct={stats.recovered_delta_pct_7d} />
            </div>
          </div>
          <div className="bg-gray-50 rounded-lg p-4">
            <p className="text-xs font-medium text-gray-500">New money at risk</p>
            <p className="text-xl font-bold text-red-600 mt-1">{formatCurrency(stats.at_risk_7d)}</p>
            <div className="mt-1">
              <Delta amount={stats.at_risk_delta_7d} pct={stats.at_risk_delta_pct_7d} invert />
            </div>
          </div>
        </div>
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
