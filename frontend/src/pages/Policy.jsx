import { useState, useEffect } from 'react';
import { getPolicyConfig, updatePolicyConfig } from '../services/api';

function InputField({ label, value, onChange, type = 'number', min, max, step, description }) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700">{label}</label>
      {description && <p className="text-xs text-gray-400 mt-0.5">{description}</p>}
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(type === 'number' ? parseFloat(e.target.value) : e.target.value)}
        min={min}
        max={max}
        step={step}
        className="mt-1 block w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
      />
    </div>
  );
}

export default function Policy() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);
  const [originalConfig, setOriginalConfig] = useState(null);

  useEffect(() => {
    async function fetchConfig() {
      try {
        const res = await getPolicyConfig();
        setConfig(res);
        setOriginalConfig(res);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    fetchConfig();
  }, []);

  const handleChange = (field, value) => {
    setConfig(prev => ({ ...prev, [field]: value }));
    setSuccess(false);
  };

  const hasChanges = () => {
    if (!config || !originalConfig) return false;
    return Object.keys(config).some(key => {
      if (key === 'id' || key === 'created_at' || key === 'updated_at' || key === 'is_active' || key === 'description') return false;
      return config[key] !== originalConfig[key];
    });
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSuccess(false);
    try {
      const updates = {};
      if (config.max_retries !== originalConfig.max_retries) updates.max_retries = config.max_retries;
      if (config.max_reminders !== originalConfig.max_reminders) updates.max_reminders = config.max_reminders;
      if (config.max_recovery_attempts !== originalConfig.max_recovery_attempts) updates.max_recovery_attempts = config.max_recovery_attempts;
      if (config.autonomous_amount_limit !== originalConfig.autonomous_amount_limit) updates.autonomous_amount_limit = config.autonomous_amount_limit;
      if (config.minimum_ai_confidence !== originalConfig.minimum_ai_confidence) updates.minimum_ai_confidence = config.minimum_ai_confidence;
      if (config.minimum_recovery_probability !== originalConfig.minimum_recovery_probability) updates.minimum_recovery_probability = config.minimum_recovery_probability;
      if (config.case_lifetime_days !== originalConfig.case_lifetime_days) updates.case_lifetime_days = config.case_lifetime_days;
      if (config.escalation_threshold !== originalConfig.escalation_threshold) updates.escalation_threshold = config.escalation_threshold;

      const res = await updatePolicyConfig(updates);
      setConfig(res);
      setOriginalConfig(res);
      setSuccess(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setConfig(originalConfig);
    setSuccess(false);
    setError(null);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  if (!config) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4">
        <p className="text-red-800 text-sm">Failed to load policy configuration</p>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Policy Settings</h1>
        <p className="text-sm text-gray-500 mt-1">
          Configure the deterministic Policy Engine rules for recovery action validation
        </p>
      </div>

      {/* Alerts */}
      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3">
          <svg className="w-5 h-5 text-red-500 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <p className="text-red-800 text-sm">{error}</p>
        </div>
      )}
      {success && (
        <div className="bg-green-50 border border-green-200 rounded-lg p-4 flex items-start gap-3">
          <svg className="w-5 h-5 text-green-500 mt-0.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
          <p className="text-green-800 text-sm">Policy configuration updated successfully</p>
        </div>
      )}

      {/* Retry & Reminder Limits */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
        <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Retry & Reminder Limits</h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <InputField
            label="Maximum Retries"
            value={config.max_retries}
            onChange={(v) => handleChange('max_retries', v)}
            min={0} max={10}
            description="Max retry attempts per case"
          />
          <InputField
            label="Maximum Reminders"
            value={config.max_reminders}
            onChange={(v) => handleChange('max_reminders', v)}
            min={0} max={10}
            description="Max payment reminders per case"
          />
          <InputField
            label="Max Recovery Attempts"
            value={config.max_recovery_attempts}
            onChange={(v) => handleChange('max_recovery_attempts', v)}
            min={0} max={20}
            description="Max total recovery actions per case"
          />
        </div>
      </div>

      {/* Thresholds */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
        <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Thresholds</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <InputField
            label="Minimum AI Confidence"
            value={config.minimum_ai_confidence}
            onChange={(v) => handleChange('minimum_ai_confidence', v)}
            min={0.1} max={1.0} step={0.05}
            description="Minimum confidence required for autonomous action (0.1 - 1.0)"
          />
          <InputField
            label="Minimum Recovery Probability"
            value={config.minimum_recovery_probability}
            onChange={(v) => handleChange('minimum_recovery_probability', v)}
            min={0.05} max={1.0} step={0.05}
            description="Minimum recovery probability required (0.05 - 1.0)"
          />
        </div>
      </div>

      {/* Amount & Time Limits */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
        <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Amount & Time Limits</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <InputField
            label="Autonomous Amount Limit (₹)"
            value={config.autonomous_amount_limit}
            onChange={(v) => handleChange('autonomous_amount_limit', v)}
            min={0} max={100000} step={500}
            description="Max amount for autonomous recovery actions"
          />
          <InputField
            label="Case Lifetime (days)"
            value={config.case_lifetime_days}
            onChange={(v) => handleChange('case_lifetime_days', v)}
            min={1} max={90}
            description="Maximum case age before expiry"
          />
        </div>
      </div>

      {/* Escalation */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
        <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Escalation</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <InputField
            label="Escalation Threshold"
            value={config.escalation_threshold}
            onChange={(v) => handleChange('escalation_threshold', v)}
            min={0.0} max={1.0} step={0.05}
            description="Confidence above this threshold triggers escalation"
          />
        </div>
      </div>

      {/* Action Buttons */}
      <div className="flex items-center justify-between">
        <p className="text-xs text-gray-400">
          Last updated: {config.updated_at ? new Date(config.updated_at).toLocaleString() : '-'}
        </p>
        <div className="flex gap-3">
          <button
            onClick={handleReset}
            disabled={!hasChanges() || saving}
            className="px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-40"
          >
            Reset
          </button>
          <button
            onClick={handleSave}
            disabled={!hasChanges() || saving}
            className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-40 flex items-center gap-2"
          >
            {saving && <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white" />}
            Save Changes
          </button>
        </div>
      </div>
    </div>
  );
}
