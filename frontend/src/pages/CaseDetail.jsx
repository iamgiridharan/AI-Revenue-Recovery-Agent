import { useState, useEffect, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { getCaseFullDetail, executeRecovery, diagnoseCase, reviewCase } from '../services/api';

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

// A case the autonomous flow must not act on again.
const SETTLED_STATUSES = new Set(['RECOVERED', 'CLOSED']);

// Actions the recovery service can actually execute. The remaining agent actions
// (NO_ACTION, WAIT_AND_RETRY, ESCALATE_TO_HUMAN, MARK_UNRECOVERABLE) are
// decisions rather than executable recovery attempts.
const EXECUTABLE_ACTIONS = ['CREATE_PAYMENT_LINK', 'RETRY', 'SEND_PAYMENT_REMINDER'];

/**
 * Run the AI agent on this case.
 *
 * The agent reads the transaction, customer history and ML prediction and returns
 * a validated recommendation. It never executes a payment: the recommendation is
 * authorised by the Policy Engine when an action is executed below.
 */
function DiagnosisPanel({ caseId, status, onDiagnosed }) {
  const [running, setRunning] = useState(false);
  const [recommendation, setRecommendation] = useState(null);
  const [error, setError] = useState(null);

  const settled = SETTLED_STATUSES.has(status);

  async function handleDiagnose() {
    setRunning(true);
    setError(null);
    setRecommendation(null);
    try {
      const res = await diagnoseCase(caseId);
      setRecommendation(res.data);
      if (onDiagnosed) onDiagnosed();
    } catch (err) {
      setError(err.message);
    } finally {
      setRunning(false);
    }
  }

  return (
    <Section title="AI Agent Diagnosis">
      <p className="text-sm text-gray-500 mb-3">
        Runs the agent on this case and records its recommendation (diagnosis,
        action, confidence, recovery probability). The recommendation is advisory:
        the Policy Engine authorises the action, never the agent.
      </p>

      <button
        onClick={handleDiagnose}
        disabled={running || settled}
        className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-40 flex items-center gap-2"
      >
        {running && <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />}
        {settled ? 'Case already settled' : running ? 'Diagnosing\u2026' : 'Run AI diagnosis'}
      </button>

      {error && (
        <div className="mt-3 bg-red-50 border border-red-200 rounded-lg p-3">
          <p className="text-xs font-semibold text-red-700 mb-0.5">Diagnosis not recorded</p>
          <p className="text-sm text-red-800">{error}</p>
        </div>
      )}

      {recommendation && (
        <div className="mt-3 border border-gray-200 rounded-lg p-3 space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold tracking-wide px-2 py-0.5 rounded bg-indigo-50 text-indigo-700 border border-indigo-200">
              RECOMMENDATION
            </span>
            <span className="text-xs text-gray-500">
              {recommendation.recommended_action?.replace(/_/g, ' ')}
              {' \u00b7 '}confidence {formatPercent(recommendation.confidence)}
              {' \u00b7 '}recovery probability {formatPercent(recommendation.recovery_probability)}
            </span>
          </div>

          <p className="text-sm text-gray-700 leading-relaxed">{recommendation.diagnosis}</p>
          {recommendation.reasoning_summary && (
            <p className="text-xs text-gray-500">{recommendation.reasoning_summary}</p>
          )}
          {recommendation.customer_message && (
            <div className="bg-gray-50 border border-gray-100 rounded-lg p-2">
              <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-0.5">
                Customer message
              </p>
              <p className="text-xs text-gray-600">{recommendation.customer_message}</p>
            </div>
          )}
          {!EXECUTABLE_ACTIONS.includes(recommendation.recommended_action) && (
            <p className="text-[11px] text-amber-700">
              This recommendation is a decision, not an executable recovery action.
              Pick an executable action below to attempt recovery.
            </p>
          )}
          <p className="text-[11px] text-gray-400">
            The policy decision for this recommendation is recorded in the audit log.
          </p>
        </div>
      )}
    </Section>
  );
}

/**
 * Execute a chosen recovery action for the case.
 *
 * The caller chooses *which* action to execute; the amount always comes from the
 * failed transaction record and the confidence / recovery probability are always
 * the recorded diagnosis values (never caller input). The backend authorises the
 * action with the Policy Engine first. When no Razorpay Test Mode credentials are
 * configured it returns `execution_mode: "MOCK"`, which means no provider call
 * was made and the link cannot be paid - shown explicitly rather than as a
 * payment.
 */
function RecoveryExecution({ caseId, status, recommendedAction, onExecuted }) {
  const recordedExecutable = EXECUTABLE_ACTIONS.includes(recommendedAction);
  const [actionType, setActionType] = useState(
    recordedExecutable ? recommendedAction : 'CREATE_PAYMENT_LINK'
  );
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const settled = SETTLED_STATUSES.has(status);
  const escalated = status === 'ESCALATED';
  const blocked = settled || escalated;

  async function handleExecute() {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const res = await executeRecovery(caseId, { action_type: actionType });
      setResult(res.data);
      if (onExecuted) onExecuted();
    } catch (err) {
      setError(err.message);
    } finally {
      setRunning(false);
    }
  }

  return (
    <Section title="Execute Recovery Action (Razorpay Test Mode)">
      <p className="text-sm text-gray-500 mb-3">
        Choose which recovery action to attempt. The Policy Engine must approve it
        before any payment link is created, the amount always comes from the failed
        transaction record, and confidence / recovery probability always come from
        the recorded AI diagnosis.
      </p>

      {/* Action picker */}
      <div className="flex flex-wrap gap-2 mb-2">
        {EXECUTABLE_ACTIONS.map((action) => {
          const selected = actionType === action;
          return (
            <button
              key={action}
              type="button"
              onClick={() => setActionType(action)}
              disabled={blocked}
              className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                selected
                  ? 'bg-blue-50 border-blue-300 text-blue-700 font-medium'
                  : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'
              } disabled:opacity-40`}
            >
              {action.replace(/_/g, ' ')}
              {action === recommendedAction && (
                <span className="ml-1 text-[10px] text-gray-400">(AI pick)</span>
              )}
            </button>
          );
        })}
      </div>

      <p className="text-xs text-gray-500 mb-3">
        {recommendedAction
          ? <>Recorded AI recommendation: <span className="font-medium text-gray-700">
              {recommendedAction.replace(/_/g, ' ')}
            </span>{!recordedExecutable && ' \u2014 a decision, not an executable action'}.</>
          : <>This case has no recorded AI recommendation yet; run the diagnosis above first.</>}
      </p>

      {escalated && (
        <div className="mb-3 bg-orange-50 border border-orange-200 rounded-lg p-3">
          <p className="text-xs font-semibold text-orange-800 mb-0.5">Awaiting human review</p>
          <p className="text-sm text-orange-800">
            This case was escalated, so the Policy Engine refuses autonomous action
            on it. Use the Human Review panel above: a named reviewer approves,
            overrides or closes it, and that decision releases this gate.
          </p>
        </div>
      )}

      <button
        onClick={handleExecute}
        disabled={running || blocked}
        className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-40 flex items-center gap-2"
      >
        {running && <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />}
        {settled
          ? 'Case already settled'
          : escalated
            ? 'Awaiting human review'
            : `Execute ${actionType.replace(/_/g, ' ').toLowerCase()}`}
      </button>

      {error && (
        <div className="mt-3 bg-red-50 border border-red-200 rounded-lg p-3">
          <p className="text-xs font-semibold text-red-700 mb-0.5">Action not executed</p>
          <p className="text-sm text-red-800">{error}</p>
        </div>
      )}

      {result && (
        <div className="mt-3 border border-gray-200 rounded-lg p-3 space-y-2">
          <div className="flex items-center gap-2">
            <span className={`text-[10px] font-bold tracking-wide px-2 py-0.5 rounded border ${
              result.mock
                ? 'bg-amber-50 text-amber-700 border-amber-200'
                : 'bg-blue-50 text-blue-700 border-blue-200'
            }`}>
              {result.execution_mode}
            </span>
            <span className="text-xs text-gray-500">
              {result.mock
                ? 'No Razorpay call was made. This is a placeholder link: no payment can be made through it.'
                : 'Real Razorpay Test Mode payment link. Test cards only - no live money moves.'}
            </span>
          </div>

          <InfoRow label="Action" value={result.action_type?.replace(/_/g, ' ')} />
          {result.message && <InfoRow label="Result" value={result.message} />}
          <InfoRow label="Amount" value={formatCurrency(result.amount)} />
          <InfoRow label="Payment Link ID" value={result.payment_link_id} />
          <InfoRow label="Provider Status" value={result.provider_status} />

          {result.payment_link && (
            <a
              href={result.payment_link}
              target="_blank"
              rel="noreferrer"
              className="inline-block text-sm text-blue-600 hover:text-blue-800 underline break-all"
            >
              {result.payment_link}
            </a>
          )}

          <p className="text-[11px] text-gray-400">
            The case is marked RECOVERED only by a verified payment webhook whose
            captured amount matches the amount due.
          </p>
        </div>
      )}
    </Section>
  );
}

/**
 * Human review of an escalated case.
 *
 * The Policy Engine refused to authorize this case on its own, so it waits for a
 * named human. APPROVE / OVERRIDE re-run the Policy Engine with the reviewer named
 * as the authorizing human - the autonomy thresholds that caused the escalation no
 * longer stop the action - while hard stops (an expired case, an exhausted attempt
 * budget) still refuse it. CLOSE / MARK_UNRECOVERABLE end the case without
 * recovering it.
 *
 * The amount and the recorded confidence are never editable here: the amount is
 * always the failed transaction's and the confidence is always the recorded
 * diagnosis's.
 */
function ReviewPanel({ caseId, status, recommendedAction, onReviewed }) {
  const escalated = status === 'ESCALATED';
  const recordedExecutable = EXECUTABLE_ACTIONS.includes(recommendedAction);

  const [reviewer, setReviewer] = useState(() => {
    try {
      return window.localStorage.getItem('reviewer') || '';
    } catch {
      return '';
    }
  });
  const [note, setNote] = useState('');
  const [actionType, setActionType] = useState(
    recordedExecutable ? recommendedAction : 'CREATE_PAYMENT_LINK'
  );
  const [pending, setPending] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  if (!escalated) return null;

  const nameOk = reviewer.trim().length >= 2;
  const reasonOk = note.trim().length >= 5;
  const actionMatchesRecorded = actionType === recommendedAction;
  const canApprove = nameOk && recordedExecutable && actionMatchesRecorded;
  const canOverride = nameOk && reasonOk && !actionMatchesRecorded;

  async function submit(decision) {
    setPending(decision);
    setError(null);
    setResult(null);
    try {
      try {
        window.localStorage.setItem('reviewer', reviewer.trim());
      } catch {
        // Storing the name is a convenience only.
      }
      const res = await reviewCase(caseId, {
        reviewer: reviewer.trim(),
        decision,
        action_type: decision === 'CLOSE' ? undefined : actionType,
        note: note.trim(),
      });
      setResult({ decision, data: res.data });
      setNote('');
    } catch (err) {
      setError(err.message);
    } finally {
      setPending(null);
      if (onReviewed) onReviewed();
    }
  }

  return (
    <Section title="Human Review">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-[10px] font-bold tracking-wide px-2 py-0.5 rounded bg-orange-50 text-orange-700 border border-orange-200">
          AWAITING REVIEW
        </span>
        <span className="text-xs text-gray-500">
          The Policy Engine escalated this case instead of acting on its own. Every
          decision below is recorded against the reviewer named here.
        </span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">
            Reviewer name
          </label>
          <input
            type="text"
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            placeholder="Who is deciding?"
            className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">
            Note / reason (required to override or write off)
          </label>
          <input
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why this decision?"
            className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm"
          />
        </div>
      </div>

      {/* Action being authorized */}
      <p className="text-xs font-medium text-gray-500 mb-1">
        Action to authorize
      </p>
      <div className="flex flex-wrap gap-2 mb-2">
        {EXECUTABLE_ACTIONS.map((action) => {
          const selected = actionType === action;
          return (
            <button
              key={action}
              type="button"
              onClick={() => setActionType(action)}
              disabled={pending !== null}
              className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                selected
                  ? 'bg-blue-50 border-blue-300 text-blue-700 font-medium'
                  : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'
              } disabled:opacity-40`}
            >
              {action.replace(/_/g, ' ')}
              {action === recommendedAction && (
                <span className="ml-1 text-[10px] text-gray-400">(AI pick)</span>
              )}
            </button>
          );
        })}
      </div>
      <p className="text-xs text-gray-500 mb-3">
        {recommendedAction
          ? <>Recorded AI recommendation: <span className="font-medium text-gray-700">
              {recommendedAction.replace(/_/g, ' ')}
            </span>{!recordedExecutable && ' \u2014 a decision, not an executable action, so it cannot be approved as-is: pick an action and override'}.</>
          : <>This case has no recorded AI recommendation; only closing it is possible.</>}
      </p>

      <div className="flex flex-wrap gap-2 mb-2">
        <button
          onClick={() => submit('APPROVE')}
          disabled={!canApprove || pending !== null}
          className="px-4 py-2 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-40 flex items-center gap-2"
        >
          {pending === 'APPROVE' && <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />}
          Approve &amp; execute
        </button>
        <button
          onClick={() => submit('OVERRIDE')}
          disabled={!canOverride || pending !== null}
          className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-40 flex items-center gap-2"
        >
          {pending === 'OVERRIDE' && <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />}
          Override &amp; execute
        </button>
      </div>
      <p className="text-[11px] text-gray-400 mb-3">
        Approve runs the recorded recommendation. Override runs the action selected
        above instead and needs a note. A hard policy stop - an expired case, an
        exhausted attempt budget - still refuses either one.
      </p>

      <div className="border-t border-gray-100 pt-3">
        <p className="text-xs font-medium text-gray-500 mb-2">
          Resolve without recovering
        </p>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => submit('CLOSE')}
            disabled={!nameOk || pending !== null}
            className="px-3 py-1.5 text-xs border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 disabled:opacity-40 flex items-center gap-2"
          >
            {pending === 'CLOSE' && <div className="animate-spin rounded-full h-3 w-3 border-b-2 border-gray-700" />}
            Close case
          </button>
          <button
            onClick={() => submit('MARK_UNRECOVERABLE')}
            disabled={!nameOk || !reasonOk || pending !== null}
            className="px-3 py-1.5 text-xs border border-red-200 rounded-lg text-red-700 hover:bg-red-50 disabled:opacity-40 flex items-center gap-2"
          >
            {pending === 'MARK_UNRECOVERABLE' && <div className="animate-spin rounded-full h-3 w-3 border-b-2 border-red-700" />}
            Mark unrecoverable
          </button>
        </div>
        <p className="text-[11px] text-gray-400 mt-2">
          Closing stops all recovery on this case without booking revenue. A settled
          case is never closed.
        </p>
      </div>

      {error && (
        <div className="mt-3 bg-red-50 border border-red-200 rounded-lg p-3">
          <p className="text-xs font-semibold text-red-700 mb-0.5">Review refused</p>
          <p className="text-sm text-red-800">{error}</p>
        </div>
      )}

      {result && (
        <div className="mt-3 border border-gray-200 rounded-lg p-3 space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold tracking-wide px-2 py-0.5 rounded bg-green-50 text-green-700 border border-green-200">
              {result.decision}
            </span>
            <span className="text-xs text-gray-500">
              Recorded against {result.data.reviewer} \u00b7 case is now{' '}
              {String(result.data.case_status).replace(/_/g, ' ')}
            </span>
          </div>

          {result.data.execution && (
            <>
              <div className="flex items-center gap-2">
                <span className={`text-[10px] font-bold tracking-wide px-2 py-0.5 rounded border ${
                  result.data.execution.mock
                    ? 'bg-amber-50 text-amber-700 border-amber-200'
                    : 'bg-blue-50 text-blue-700 border-blue-200'
                }`}>
                  {result.data.execution.execution_mode}
                </span>
                <span className="text-xs text-gray-500">
                  {result.data.execution.mock
                    ? 'No Razorpay call was made: placeholder link, no payment possible.'
                    : 'Real Razorpay Test Mode payment link.'}
                </span>
              </div>
              <InfoRow label="Action executed" value={result.data.execution.action_type?.replace(/_/g, ' ')} />
              <InfoRow label="Amount" value={formatCurrency(result.data.execution.amount)} />
              {result.data.execution.message && (
                <InfoRow label="Result" value={result.data.execution.message} />
              )}
              {result.data.execution.payment_link && (
                <a
                  href={result.data.execution.payment_link}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-block text-sm text-blue-600 hover:text-blue-800 underline break-all"
                >
                  {result.data.execution.payment_link}
                </a>
              )}
            </>
          )}

          {result.data.policy_decision && (
            <p className="text-[11px] text-gray-400">
              Policy: {result.data.policy_decision.decision} \u2014 {result.data.policy_decision.reason}
            </p>
          )}
        </div>
      )}
    </Section>
  );
}

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

const COST_SOURCE_LABELS = {
  not_configured: 'not configured — net recovery not computed',
  configured_template: 'configured (deployment template)',
  caller_supplied: 'supplied by caller',
};

const PROBABILITY_SOURCE_LABELS = {
  case_level_ml_model: 'ML model (case-level, not action-specific)',
  caller_supplied: 'supplied by caller',
  unavailable: 'unavailable — estimate not computed',
};

function formatPercent(value) {
  if (value === null || value === undefined) return '-';
  return `${Math.round(value * 100)}%`;
}

function formatEstimate(value) {
  if (value === null || value === undefined) return 'not computed';
  return formatCurrency(value);
}

function ValueAssessment({ assessment }) {
  if (!assessment) return null;

  const inputs = assessment.inputs || {};
  const penalties = assessment.penalties || {};
  const candidates = assessment.candidates || [];
  const excluded = assessment.excluded_candidates || [];

  return (
    <Section title="Estimated Recovery Value">
      {/* Explicit estimate labelling */}
      <div className="flex items-center gap-2 mb-3">
        <span className="text-[10px] font-bold tracking-wide px-2 py-0.5 rounded bg-indigo-50 text-indigo-700 border border-indigo-200">
          {assessment.estimate_label || 'ESTIMATE'}
        </span>
        <span className="text-xs text-gray-500">
          Estimates only. Actual recovered revenue is {formatCurrency(assessment.actual_recovered_amount)}
          {' '}and is set only by a verified payment webhook.
        </span>
      </div>

      {/* Formula, shown exactly as executed */}
      <div className="bg-gray-50 border border-gray-100 rounded-lg p-3 mb-3 space-y-1">
        <p className="text-xs font-mono text-gray-600">
          expected gross = {formatCurrency(inputs.payment_amount)} × {formatPercent(inputs.recovery_probability)}
        </p>
        <p className="text-xs font-mono text-gray-600">
          expected net = expected gross − intervention cost
        </p>
        <p className="text-xs font-mono text-gray-600">
          utility score = expected net − configured penalties
        </p>
      </div>

      {/* Inputs and their provenance */}
      <div className="grid grid-cols-2 gap-x-6 mb-3">
        <InfoRow label="Payment Amount" value={formatCurrency(inputs.payment_amount)} />
        <InfoRow label="Recovery Probability" value={formatPercent(inputs.recovery_probability)} />
        <InfoRow
          label="Probability Source"
          value={PROBABILITY_SOURCE_LABELS[inputs.recovery_probability_source] || inputs.recovery_probability_source}
        />
        <InfoRow
          label="Intervention Cost Source"
          value={COST_SOURCE_LABELS[assessment.intervention_cost_source] || assessment.intervention_cost_source}
        />
      </div>

      {/* Figures, kept separately inspectable */}
      <div className="grid grid-cols-2 gap-3 mb-3">
        <div className="border border-gray-100 rounded-lg p-3">
          <p className="text-xs text-gray-500">Estimated Gross Recovery</p>
          <p className="text-lg font-semibold text-gray-900">{formatEstimate(assessment.expected_gross_recovery)}</p>
        </div>
        <div className="border border-gray-100 rounded-lg p-3">
          <p className="text-xs text-gray-500">Estimated Intervention Cost</p>
          <p className="text-lg font-semibold text-gray-900">{formatEstimate(assessment.intervention_cost)}</p>
        </div>
        <div className="border border-gray-100 rounded-lg p-3">
          <p className="text-xs text-gray-500">Estimated Net Recovery</p>
          <p className="text-lg font-semibold text-gray-900">{formatEstimate(assessment.expected_net_recovery)}</p>
        </div>
        <div className="border border-gray-100 rounded-lg p-3">
          <p className="text-xs text-gray-500">Utility Score (penalties applied)</p>
          <p className="text-lg font-semibold text-gray-900">{formatEstimate(assessment.utility_score)}</p>
          <p className="text-[10px] text-gray-400 mt-0.5">
            not a monetary figure; penalties: {formatCurrency(penalties.total_penalties || 0)}
          </p>
        </div>
      </div>

      {assessment.figures_scope && (
        <p className="text-xs text-gray-500 mb-3">{assessment.figures_scope}</p>
      )}
      {assessment.ranking_note && (
        <p className="text-xs text-gray-500 mb-3">{assessment.ranking_note}</p>
      )}

      {/* Candidate ranking (advisory only) */}
      {candidates.length > 0 ? (
        <div className="mb-3">
          <p className="text-xs font-semibold text-gray-600 mb-2 uppercase tracking-wide">
            Ranked eligible candidates
          </p>
          <div className="space-y-2">
            {candidates.map((candidate) => (
              <div key={candidate.action} className="border border-gray-100 rounded-lg p-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-medium text-gray-900">
                    #{candidate.rank} {candidate.action.replace(/_/g, ' ')}
                  </span>
                  {candidate.action === assessment.recommended_action && (
                    <span className="text-xs px-2 py-0.5 rounded bg-green-50 text-green-700">highest value</span>
                  )}
                </div>
                <div className="text-xs text-gray-500">
                  gross {formatEstimate(candidate.expected_gross_recovery)} · cost{' '}
                  {formatEstimate(candidate.intervention_cost)} · net{' '}
                  {formatEstimate(candidate.expected_net_recovery)} · utility{' '}
                  {formatEstimate(candidate.utility_score)}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <p className="text-sm text-gray-500 mb-3">
          No eligible recovery action for this case.
        </p>
      )}

      {/* Excluded candidates with reasons */}
      {excluded.length > 0 && (
        <div className="mb-3">
          <p className="text-xs font-semibold text-gray-600 mb-2 uppercase tracking-wide">
            Excluded from ranking
          </p>
          <div className="space-y-1">
            {excluded.map((candidate) => (
              <div key={candidate.action} className="text-xs text-gray-500">
                <span className="font-medium text-gray-700">{candidate.action.replace(/_/g, ' ')}</span>
                {' — '}{candidate.eligibility_reason}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Assumptions and limitations, so the estimate is not over-read */}
      {assessment.assumptions && assessment.assumptions.length > 0 && (
        <div className="mb-2">
          <p className="text-xs font-semibold text-gray-600 mb-1">Assumptions</p>
          <ul className="list-disc list-inside space-y-0.5">
            {assessment.assumptions.map((line, idx) => (
              <li key={idx} className="text-[11px] text-gray-500">{line}</li>
            ))}
          </ul>
        </div>
      )}
      {assessment.limitations && assessment.limitations.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-gray-600 mb-1">Limitations</p>
          <ul className="list-disc list-inside space-y-0.5">
            {assessment.limitations.map((line, idx) => (
              <li key={idx} className="text-[11px] text-gray-500">{line}</li>
            ))}
          </ul>
        </div>
      )}
    </Section>
  );
}

export default function CaseDetail() {
  const { id } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchDetail = useCallback(async () => {
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
  }, [id]);

  useEffect(() => {
    fetchDetail();
  }, [fetchDetail]);

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

  const { case: caseData, customer, transaction, recovery_actions, audit_events, value_assessment } = data;

  // Keep the estimate and the actual recovered revenue visibly distinct.
  const valueAssessment = value_assessment
    ? { ...value_assessment, actual_recovered_amount: caseData.recovered_amount }
    : null;

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

          {/* Run the agent on this case */}
          <DiagnosisPanel
            caseId={caseData.case_id}
            status={caseData.status}
            onDiagnosed={fetchDetail}
          />

          {/* Estimated recovery value (estimates only) */}
          <ValueAssessment assessment={valueAssessment} />

          {/* Human review (escalated cases only) */}
          <ReviewPanel
            caseId={caseData.case_id}
            status={caseData.status}
            recommendedAction={caseData.recommended_action}
            onReviewed={fetchDetail}
          />

          {/* Controlled recovery execution (Phase 4) */}
          <RecoveryExecution
            caseId={caseData.case_id}
            status={caseData.status}
            recommendedAction={caseData.recommended_action}
            onExecuted={fetchDetail}
          />

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
