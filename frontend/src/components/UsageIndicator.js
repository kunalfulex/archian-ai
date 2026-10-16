import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { FiCreditCard, FiAlertTriangle } from 'react-icons/fi';
import api from '../api';

const UsageIndicator = () => {
  const navigate = useNavigate();
  const [currentPlan, setCurrentPlan] = useState(null);
  const [currentUsage, setCurrentUsage] = useState(null);
  const [loading, setLoading] = useState(true);
  const hasInitialized = useRef(false); // Use ref to prevent duplicate calls

  useEffect(() => {
    // Prevent multiple executions using ref
    if (hasInitialized.current) {
      return;
    }
    hasInitialized.current = true;

    fetchPlanData();
  }, []);

  const fetchPlanData = async () => {
    try {
      const response = await api.get('/user-plan');
      if (response.data.status === 'success') {
        setCurrentPlan(response.data.plan);
        setCurrentUsage(response.data.usage);
      }
    } catch (error) {
      console.error('Error fetching plan data:', error);
    } finally {
      setLoading(false);
    }
  };

  if (loading || !currentPlan || !currentUsage) {
    return null;
  }

  const getUsagePercentage = (current, limit) => {
    if (current == null || limit == null || limit <= 0 || limit === -1) return 0;
    return Math.min((current / limit) * 100, 100);
  };

  const getUsageColor = (percentage) => {
    if (percentage >= 90) return '#ef4444'; // Red
    if (percentage >= 75) return '#f59e0b'; // Yellow
    return '#10b981'; // Green
  };

  const planLabel = (currentPlan.plan_type || '').replace(/_/g, ' ');
  const planTitle = planLabel.charAt(0).toUpperCase() + planLabel.slice(1) + ' Plan';
  const creditBalance = currentPlan.credit_balance != null ? Number(currentPlan.credit_balance) : 0;
  const creditsUsed = currentUsage.credits_used != null ? Number(currentUsage.credits_used) : 0;
  const creditsPerMonth = currentPlan.credits_per_month != null ? Number(currentPlan.credits_per_month) : null;
  const hasMonthlyAllowance = creditsPerMonth != null && creditsPerMonth > 0 && creditsPerMonth !== -1;
  const creditsUsagePct = hasMonthlyAllowance ? getUsagePercentage(creditsUsed, creditsPerMonth) : 0;
  const isNearLimit = hasMonthlyAllowance && creditsUsagePct >= 75;
  const maxFileSizeMb = currentPlan.max_file_size_mb;
  const maxFileSizeLabel = maxFileSizeMb == null || maxFileSizeMb === -1 ? 'Unlimited' : `${maxFileSizeMb}MB`;
  const periodEndLabel = currentPlan.current_period_end
    ? new Date(currentPlan.current_period_end).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      })
    : null;

  return (
    <div 
      className="usage-indicator"
      style={{
        backgroundColor: '#fff',
        border: '1px solid #e5e7eb',
        borderRadius: '12px',
        padding: '16px',
        marginBottom: '24px',
        boxShadow: '0 2px 4px rgba(0, 0, 0, 0.05)'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <FiCreditCard size={20} color="#6b7280" />
          <div>
            <h4 style={{ margin: '0 0 4px 0', fontSize: '16px', fontWeight: '600', color: '#1f2937' }}>
              {planTitle}
            </h4>
            <p style={{ margin: 0, fontSize: '14px', color: '#6b7280' }}>
              Credits
            </p>
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '4px' }}>
          <span style={{ fontSize: '12px', color: '#6b7280', fontWeight: '500' }}>
            Max file size: {maxFileSizeLabel}
          </span>
          {isNearLimit && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#f59e0b' }}>
              <FiAlertTriangle size={16} />
              <span style={{ fontSize: '12px', fontWeight: '500' }}>Near limit</span>
            </div>
          )}
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', marginBottom: '16px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: '12px', color: '#6b7280' }}>Credit balance</span>
          <span style={{ fontSize: '16px', fontWeight: '600', color: '#1f2937' }}>
            {creditBalance.toLocaleString()}
          </span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: '12px', color: '#6b7280' }}>Used this month</span>
          <span style={{ fontSize: '14px', fontWeight: '500', color: '#374151' }}>
            {creditsUsed.toLocaleString()}
            {hasMonthlyAllowance && (
              <span style={{ fontSize: '12px', color: '#6b7280', fontWeight: '400' }}>
                {' '}/ {creditsPerMonth.toLocaleString()} included
              </span>
            )}
          </span>
        </div>
        {hasMonthlyAllowance && (
          <div style={{ marginTop: '4px' }}>
            <div style={{ 
              width: '100%', 
              height: '6px', 
              backgroundColor: '#f3f4f6', 
              borderRadius: '3px',
              overflow: 'hidden'
            }}>
              <div style={{
                width: `${creditsUsagePct}%`,
                height: '100%',
                backgroundColor: getUsageColor(creditsUsagePct),
                borderRadius: '3px',
                transition: 'width 0.3s ease'
              }} />
            </div>
          </div>
        )}
      </div>

      {currentPlan.payment_issue && (
        <p style={{ margin: '0 0 12px', fontSize: '13px', color: '#b45309', lineHeight: 1.4 }}>
          Payment failed — update billing to keep your plan. Credits remain available while we retry.
        </p>
      )}
      {currentPlan.cancel_at_period_end && periodEndLabel && (
        <p style={{ margin: '0 0 12px', fontSize: '13px', color: '#6b7280', lineHeight: 1.4 }}>
          Credits valid until {periodEndLabel}. After that, your balance will adjust to your free plan allowance.
        </p>
      )}

      <button
        onClick={() => navigate('/plans')}
        style={{
          width: '100%',
          backgroundColor: '#f3f4f6',
          color: '#374151',
          border: 'none',
          padding: '10px 16px',
          borderRadius: '8px',
          fontSize: '14px',
          fontWeight: '500',
          cursor: 'pointer',
          transition: 'all 0.2s ease'
        }}
        onMouseEnter={(e) => {
          e.target.style.backgroundColor = '#e5e7eb';
        }}
        onMouseLeave={(e) => {
          e.target.style.backgroundColor = '#f3f4f6';
        }}
      >
        Manage Plan
      </button>
    </div>
  );
};

export default UsageIndicator;
