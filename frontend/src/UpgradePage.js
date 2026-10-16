import React, { useState, useEffect } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { FiCheck, FiCreditCard, FiLoader } from 'react-icons/fi';
import api from './api';
import { handleStripeCheckoutResponse, handleStripeCheckoutError } from './utils/stripeCheckout';

const DEBUG = true; // set to false to disable console logs
const log = (...args) => { if (DEBUG) console.log('[UpgradePage]', ...args); };

const UpgradePage = () => {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = searchParams.get('token');

  log('render', { token: token ? `${token.slice(0, 8)}...` : null, hasToken: !!token });

  const [status, setStatus] = useState('loading'); // loading | invalid | wrong_user | ready
  const [planDetails, setPlanDetails] = useState(null);
  const [planType, setPlanType] = useState('');
  const [billingCycle, setBillingCycle] = useState('monthly');
  const [isProcessing, setIsProcessing] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    log('useEffect start', { token: token ? `${token.slice(0, 8)}...` : null });
    if (!token || !token.trim()) {
      log('no token, set invalid');
      setStatus('invalid');
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        log('calling /upgrade/validate');
        const res = await api.get('/upgrade/validate', { params: { token: token.trim() } });
        const data = res.data;
        log('validate response', { valid: data?.valid, hasPlanDetails: !!data?.plan_details, userId: data?.user_id });
        if (!data.valid || !data.plan_details) {
          if (!cancelled) { log('invalid: valid or plan_details missing'); setStatus('invalid'); }
          return;
        }
        if (!cancelled) {
          setPlanDetails(data.plan_details);
          setPlanType(data.plan_type || '');
          log('set planDetails, planType');
        }
        log('calling /user-plan');
        const planRes = await api.get('/user-plan').catch((e) => { log('user-plan error', e?.response?.status, e?.message); return null; });
        if (cancelled) return;
        if (!planRes || planRes.data.status !== 'success') {
          log('invalid: user-plan failed or not success', planRes?.data?.status);
          setStatus('invalid');
          return;
        }
        const currentUserId = planRes.data.user_id;
        log('user check', { currentUserId, linkUserId: data.user_id, match: currentUserId === data.user_id });
        if (currentUserId !== data.user_id) {
          log('wrong_user');
          setStatus('wrong_user');
          return;
        }
        log('set ready');
        setStatus('ready');
      } catch (e) {
        log('catch', e?.response?.status, e?.response?.data, e?.message);
        if (!cancelled) setStatus('invalid');
      }
    })();
    return () => { cancelled = true; };
  }, [token]);

  const handleUpgrade = async () => {
    if (!planType || !token) return;
    setIsProcessing(true);
    setError(null);
    try {
      const response = await api.post('/stripe/create-checkout-session', {
        plan_type: planType,
        billing_cycle: billingCycle,
        token: token.trim(),
      });
      if (!handleStripeCheckoutResponse(response)) {
        setError(response.data?.message || 'Failed to create checkout session');
      }
    } catch (err) {
      const { message, redirected } = handleStripeCheckoutError(err);
      if (!redirected) {
        setError(message);
      }
    } finally {
      setIsProcessing(false);
    }
  };

  const selectedPlan = planDetails;

  log('render branch', { status, hasPlanDetails: !!planDetails });

  if (status === 'loading') {
    return (
      <div style={{ textAlign: 'center', padding: '60px 20px' }}>
        <FiLoader size={40} className="animate-spin" style={{ marginBottom: 16, color: '#3b82f6' }} />
        <p>Validating your link...</p>
      </div>
    );
  }

  if (status === 'invalid') {
    return (
      <div style={{ textAlign: 'center', padding: '60px 20px', maxWidth: 500, margin: '0 auto' }}>
        <h1 style={{ color: '#dc2626', marginBottom: 12 }}>Invalid or expired link</h1>
        <p style={{ color: '#6b7280', marginBottom: 24 }}>This upgrade link is invalid or has already been used.</p>
        <button
          onClick={() => navigate('/plans')}
          style={{
            padding: '12px 24px',
            backgroundColor: '#3b82f6',
            color: 'white',
            border: 'none',
            borderRadius: 8,
            fontSize: 16,
            cursor: 'pointer',
          }}
        >
          View plans
        </button>
      </div>
    );
  }

  if (status === 'wrong_user') {
    return (
      <div style={{ textAlign: 'center', padding: '60px 20px', maxWidth: 500, margin: '0 auto' }}>
        <h1 style={{ color: '#dc2626', marginBottom: 12 }}>Wrong account</h1>
        <p style={{ color: '#6b7280' }}>This link was sent for another account. Please log in with the correct account.</p>
      </div>
    );
  }

  if (status !== 'ready' || !selectedPlan) {
    return (
      <div style={{ textAlign: 'center', padding: '60px 20px' }}>Loading...</div>
    );
  }

  const features = Array.isArray(selectedPlan.features) ? selectedPlan.features : [];
  const priceMonthly = selectedPlan.price_monthly != null ? Number(selectedPlan.price_monthly) : 0;
  const priceYearly = selectedPlan.price_yearly != null ? Number(selectedPlan.price_yearly) : 0;

  return (
    <div style={{ maxWidth: 500, margin: '0 auto', padding: 24 }}>
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <div style={{
            backgroundColor: '#3b82f6',
            borderRadius: '50%',
            width: 64,
            height: 64,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 16px',
          }}>
            <FiCreditCard size={32} color="white" />
          </div>
          <h2 style={{ margin: '0 0 8px 0', fontSize: 24, fontWeight: 600, color: '#1f2937' }}>
            Upgrade to {planType?.toLowerCase() === 'free' ? 'Hobby' : `${planType?.charAt(0)?.toUpperCase()}${planType?.slice(1)}`} Plan
          </h2>
          <p style={{ margin: 0, color: '#6b7280', fontSize: 16 }}>Unlock more features and higher limits</p>
        </div>

        <div style={{ marginBottom: 32 }}>
          <div style={{
            backgroundColor: '#f8fafc',
            border: '2px solid #e2e8f0',
            borderRadius: 12,
            padding: 24,
            textAlign: 'center',
          }}>
            <div style={{ fontSize: 32, fontWeight: 700, color: '#1f2937', marginBottom: 8 }}>
              {selectedPlan.plan_type?.toLowerCase() === 'free' ? 'Free' : `$${priceMonthly}/mo.`}
            </div>
            {priceYearly > 0 && (
              <div style={{ fontSize: 16, color: '#6b7280', marginBottom: 16 }}>${priceYearly}/yr.</div>
            )}
            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: 14, color: '#6b7280', marginBottom: 4 }}>Credits per month</div>
              <div style={{ fontSize: 18, fontWeight: 600, color: '#1f2937' }}>
                {selectedPlan.credits_per_month == null
                  ? '—'
                  : selectedPlan.credits_per_month === -1
                    ? (selectedPlan.plan_type?.toLowerCase() === 'enterprise' ? 'Custom' : 'Unlimited')
                    : selectedPlan.credits_per_month === 0 && selectedPlan.plan_type?.toLowerCase() === 'free'
                      ? '500 (one-time at signup)'
                      : selectedPlan.credits_per_month === 0
                        ? '—'
                        : Number(selectedPlan.credits_per_month).toLocaleString()}
              </div>
            </div>
            {features.length > 0 && (
              <div style={{ textAlign: 'left' }}>
                <h4 style={{ margin: '0 0 12px 0', fontSize: 16, fontWeight: 600, color: '#374151' }}>Features included:</h4>
                <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
                  {features.map((feature, index) => (
                    <li key={index} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', color: '#6b7280', fontSize: 14 }}>
                      <FiCheck size={16} color="#10b981" />
                      {String(feature).replace(/records\/run/g, 'records/month').replace(/per run/g, 'per month')}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>

        {priceYearly > 0 && (
          <div style={{ marginBottom: 24 }}>
            <h4 style={{ margin: '0 0 12px 0', fontSize: 16, fontWeight: 600, color: '#374151' }}>Choose Billing Cycle:</h4>
            <div style={{ display: 'flex', gap: 12 }}>
              <label style={{
                display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', padding: '12px 16px',
                border: `2px solid ${billingCycle === 'monthly' ? '#3b82f6' : '#e5e7eb'}`,
                borderRadius: 8, backgroundColor: billingCycle === 'monthly' ? '#eff6ff' : 'white', flex: 1, justifyContent: 'center',
              }}>
                <input type="radio" name="billingCycle" value="monthly" checked={billingCycle === 'monthly'} onChange={(e) => setBillingCycle(e.target.value)} style={{ display: 'none' }} />
                <span style={{ fontWeight: 500, color: billingCycle === 'monthly' ? '#3b82f6' : '#6b7280' }}>Monthly ({selectedPlan.plan_type?.toLowerCase() === 'free' ? 'Free' : `$${priceMonthly}/mo.`})</span>
              </label>
              <label style={{
                display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', padding: '12px 16px',
                border: `2px solid ${billingCycle === 'yearly' ? '#3b82f6' : '#e5e7eb'}`,
                borderRadius: 8, backgroundColor: billingCycle === 'yearly' ? '#eff6ff' : 'white', flex: 1, justifyContent: 'center',
              }}>
                <input type="radio" name="billingCycle" value="yearly" checked={billingCycle === 'yearly'} onChange={(e) => setBillingCycle(e.target.value)} style={{ display: 'none' }} />
                <span style={{ fontWeight: 500, color: billingCycle === 'yearly' ? '#3b82f6' : '#6b7280' }}>Yearly (${priceYearly}/yr.)</span>
              </label>
            </div>
          </div>
        )}

        {error && (
          <div style={{ backgroundColor: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, padding: '12px 16px', marginBottom: 24, color: '#dc2626', fontSize: 14 }}>
            {error}
          </div>
        )}

        <button
          onClick={handleUpgrade}
          disabled={isProcessing}
          style={{
            width: '100%',
            backgroundColor: '#3b82f6',
            color: 'white',
            border: 'none',
            padding: '14px 24px',
            borderRadius: 8,
            fontSize: 16,
            fontWeight: 600,
            cursor: isProcessing ? 'not-allowed' : 'pointer',
            opacity: isProcessing ? 0.6 : 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
          }}
        >
          {isProcessing ? (
            <>
              <FiLoader size={20} className="animate-spin" />
              Processing...
            </>
          ) : (
            <>
              <FiCreditCard size={20} />
              Upgrade Now
            </>
          )}
        </button>

        <div style={{ textAlign: 'center', marginTop: 24, padding: 16, backgroundColor: '#f9fafb', borderRadius: 8 }}>
          <p style={{ margin: 0, fontSize: 14, color: '#6b7280' }}>Secure payment processing with Stripe. You can cancel anytime.</p>
        </div>
      </div>
  );
};

export default UpgradePage;
