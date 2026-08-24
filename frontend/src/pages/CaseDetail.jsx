import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { getCaseFullDetail } from '../services/api';

const STATUS_STYLES = {
  OPEN: 'bg-blue-50 text-blue-700 border-blue-200',
  IN_PROGRESS: 'bg-amber-50 text-amber-700 border-amber-200',
  RECOVERY_ATTEMPTED: 'bg-purple-50 text-purple-700 border-purple-200',
  RECOVERED: 'bg-green-50 text-green-700 border-green-200',
  FAILED: 'bg-red-50 text-red-700 border-red-200',
  ESCALATED: 'bg-orange-50 text-orange-700 border-orange-200',
  CLOSED: 'bg-gray-50 text-gray-700 border-gray-200',
};

const TIMELINE_STEPS = [
  { key: 'CASE_CREATED', label: 'Payment Failed', icon: 'X' },
  { key: 'RISK_ASSESSED', label: 'Risk Detected', icon: '!' },
  { key: 'DIAGNOSIS_COMPLETED', label: 'AI Analysis', icon: 'AI' },
  { key: 'ACTION_RECOMMENDED', label: 'Recovery Recommended', icon: 'R' },
  { key: 'POLICY_CHECKED', label: 'Policy Check', icon: 'P' },
  { key: 'ACTION_EXECUTED', label: 'Action Executed', icon: 'A' },
  { key: 'PAYMENT_RECEIVED', label: 'Payment Result', icon: '$' },
];

function formatCurrency(amount) {
  if (amount === null || amount === undefined) return '-';
  return '₹' + Number(amount).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatDate(isoString) {
  if (!isoString) return '-';
  return new Date(isoString).toLocaleString('en-IN', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function InfoRow({ label, value }) {
  return (
    <div className="flex justify-between py-1.5">
      <span className="text-sm text-gray-500">{label}</span>
      <span className="text-sm text-gray-900 font-medium">{value || '-'}</span>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      <h3 className="text-sm font-semibold text-gray-700 mb-3 uppercase tracking-wide">{title}</h3>
      {children}
    </div>
  );
}

function Timeline({ events }) {
  if (!events || events.length === 0) {
    return <p className="text-sm text-gray-400">No events recorded yet</p>;
  }

  // Map events to timeline steps
  const eventTypes = new Set(events.map(e => e.event_type));

  return (
    <div className="relative">
      <div className="absolute left-4 top-0 bottom-0 w-0.5 bg-gray-200" />
      <div className="space-y-4">
        {TIMELINE_STEPS.map((step, idx) => {
          const completed = eventTypes.has(step.key);
          const event = events.find(e => e.event_type === step.key);
          return (
            <div key={step.key} className="relative flex items-start gap-4">
              <div className={`relative z-10 w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold border-2 ${
                completed
                  ? 'bg-green-500 border-green-500 text-white'
                  : 'bg-white border-gray-300 text-gray-400'
              }`}>
                {step.icon}
              </div>
              <div className="flex-1 pt-1">
                <p className={`text-sm font-medium ${completed ? 'text-gray-900' : 'text-gray-400'}`}>
                  {step.label}
                </p>
                {event && (
                  <p className="text-xs text-gray-500 mt-0.5">
                    {event.actor} — {event.decision || event.result || ''}
                    {event.timestamp && ` — ${formatDate(event.timestamp)}`}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function CaseDetail() {
  const { id } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function fetchDetail() {
      try {
        const res = await getCaseFullDetail(id);
        if (res.success) {
          setData(res.data);
        } else {
          setError(res.error?.message || 'Case not found');
        }
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    fetchDetail();
  }, [id]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <Link to="/cases" className="text-blue-600 hover:text-blue-800 text-sm mb-4 inline-block">&larr; Back to Cases</Link>
        <div className="bg-red-50 border border-red-200 rounded-lg p-4">
          <p className="text-red-800 text-sm">{error}</p>
        </div>
      </div>
    );
  }

  if (!data) return null;

  const { case: caseData, customer, transaction, recovery_actions, audit_events } = data;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to="/cases" className="text-blue-600 hover:text-blue-800 text-xs mb-2 inline-block">&larr; Back to Cases</Link>
          <h1 className="text-2xl font-bold text-gray-900">Case {caseData.case_id}</h1>
          <p className="text-sm text-gray-500 mt-1">Created {formatDate(caseData.created_at)}</p>
        </div>
        <div className="flex items-center gap-3">
          <span className={`inline-block px-3 py-1 rounded-full border text-sm font-medium ${STATUS_STYLES[caseData.status] || ''}`}>
            {caseData.status?.replace(/_/g, ' ')}
          </span>
          <span className={`inline-block px-3 py-1 rounded text-sm font-medium ${
            caseData.priority === 'CRITICAL' ? 'bg-red-50 text-red-700' :
            caseData.priority === 'HIGH' ? 'bg-amber-50 text-amber-700' :
            caseData.priority === 'MEDIUM' ? 'bg-blue-50 text-blue-700' :
            'bg-gray-50 text-gray-700'
          }`}>
            {caseData.priority}
          </span>
        </div>
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Case & ML Info */}
        <div className="lg:col-span-2 space-y-6">
          {/* Case Info */}
          <Section title="Case Information">
            <div className="grid grid-cols-2 gap-x-6">
              <InfoRow label="Amount at Risk" value={formatCurrency(caseData.amount)} />
              <InfoRow label="Recovered Amount" value={formatCurrency(caseData.recovered_amount)} />
              <InfoRow label="Risk Score" value={caseData.risk_score !== null ? `${Math.round(caseData.risk_score * 100)}%` : '-'} />
              <InfoRow label="Recovery Probability" value={caseData.recovery_probability !== null ? `${Math.round(caseData.recovery_probability * 100)}%` : '-'} />
              <InfoRow label="Attempt Count" value={caseData.attempt_count} />
              <InfoRow label="Recommended Action" value={caseData.recommended_action?.replace(/_/g, ' ') || '-'} />
            </div>
          </Section>

          {/* Diagnosis */}
          {caseData.diagnosis && (
            <Section title="AI Diagnosis">
              <p className="text-sm text-gray-700 leading-relaxed">{caseData.diagnosis}</p>
            </Section>
          )}

          {/* Transaction */}
          {transaction && (
            <Section title="Transaction Details">
              <div className="grid grid-cols-2 gap-x-6">
                <InfoRow label="Transaction ID" value={transaction.transaction_id} />
                <InfoRow label="Amount" value={formatCurrency(transaction.amount)} />
                <InfoRow label="Payment Method" value={transaction.payment_method?.toUpperCase()} />
                <InfoRow label="Failure Reason" value={transaction.failure_reason?.replace(/_/g, ' ')} />
                <InfoRow label="Attempt Count" value={transaction.attempt_count} />
                <InfoRow label="Status" value={transaction.status} />
              </div>
            </Section>
          )}

          {/* Recovery Actions */}
          <Section title="Recovery Actions">
            {recovery_actions && recovery_actions.length > 0 ? (
              <div className="space-y-3">
                {recovery_actions.map((action) => (
                  <div key={action.id} className="border border-gray-100 rounded-lg p-3">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm font-medium text-gray-900">
                        {action.action_type?.replace(/_/g, ' ')}
                      </span>
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        action.execution_status === 'SUCCESS' ? 'bg-green-50 text-green-700' :
                        action.execution_status === 'BLOCKED_BY_POLICY' ? 'bg-amber-50 text-amber-700' :
                        action.execution_status === 'FAILED' ? 'bg-red-50 text-red-700' :
                        'bg-gray-50 text-gray-600'
                      }`}>
                        {action.execution_status?.replace(/_/g, ' ')}
                      </span>
                    </div>
                    <div className="text-xs text-gray-500 space-y-0.5">
                      {action.policy_result && <p>Policy: {action.policy_result}</p>}
                      {action.confidence && <p>Confidence: {Math.round(action.confidence * 100)}%</p>}
                      {action.api_reference && <p>Ref: {action.api_reference}</p>}
                      {action.created_at && <p>{formatDate(action.created_at)}</p>}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-400">No recovery actions yet</p>
            )}
          </Section>
        </div>

        {/* Right: Customer, Timeline, Audit */}
        <div className="space-y-6">
          {/* Customer */}
          {customer && (
            <Section title="Customer">
              <div className="space-y-0.5">
                <InfoRow label="Name" value={customer.name} />
                <InfoRow label="Email" value={customer.email} />
                {customer.phone && <InfoRow label="Phone" value={customer.phone} />}
                <InfoRow label="Total Transactions" value={customer.total_transactions} />
                <InfoRow label="Successful" value={customer.successful_transactions} />
                <InfoRow label="Failed" value={customer.failed_transactions} />
                <InfoRow label="Lifetime Value" value={formatCurrency(customer.lifetime_value)} />
              </div>
            </Section>
          )}

          {/* Timeline */}
          <Section title="Recovery Timeline">
            <Timeline events={audit_events} />
          </Section>

          {/* Audit Log */}
          <Section title="Audit Log">
            {audit_events && audit_events.length > 0 ? (
              <div className="space-y-2 max-h-80 overflow-y-auto">
                {audit_events.map((event) => (
                  <div key={event.id} className="border-l-2 border-gray-200 pl-3 py-1">
                    <p className="text-xs font-medium text-gray-700">
                      {event.event_type?.replace(/_/g, ' ')}
                    </p>
                    <p className="text-xs text-gray-500">
                      {event.actor} — {event.decision || event.result || ''}
                    </p>
                    {event.reason && (
                      <p className="text-xs text-gray-400 mt-0.5 line-clamp-2">{event.reason}</p>
                    )}
                    <p className="text-[10px] text-gray-400 mt-0.5">{formatDate(event.timestamp)}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-400">No audit events</p>
            )}
          </Section>
        </div>
      </div>
    </div>
  );
}
