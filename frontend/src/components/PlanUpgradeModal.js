import React, { useState } from 'react';
import { FiX, FiCheck, FiCreditCard, FiLoader } from 'react-icons/fi';
import api from '../api';
import { handleStripeCheckoutResponse, handleStripeCheckoutError } from '../utils/stripeCheckout';

const PlanUpgradeModal = ({ isOpen, onClose, selectedPlan, onUpgrade }) => {
  const [billingCycle, setBillingCycle] = useState('monthly');
  const [isProcessing, setIsProcessing] = useState(false);
  const [error, setError] = useState(null);

  if (!isOpen) return null;

  const handleUpgrade = async () => {
    try {
      setIsProcessing(true);
      setError(null);
      
      // Create Stripe checkout session
      const response = await api.post('/stripe/create-checkout-session', {
        plan_type: selectedPlan.plan_type,
        billing_cycle: billingCycle
      });
      
      if (!handleStripeCheckoutResponse(response)) {
        setError(response.data.message || 'Failed to create checkout session');
      }
    } catch (err) {
      console.error('Error creating checkout session:', err);
      const { message, redirected } = handleStripeCheckoutError(err);
      if (!redirected) {
        setError(message);
      }
    } finally {
      setIsProcessing(false);
    }
  };

  return (
    <div className="modal-overlay" style={{
      position: 'fixed',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      backgroundColor: 'rgba(0, 0, 0, 0.5)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 1000
    }}>
      <div className="modal-content" style={{
        backgroundColor: 'white',
        borderRadius: '16px',
        padding: '32px',
        maxWidth: '500px',
        width: '90%',
        maxHeight: '90vh',
        overflow: 'auto',
        position: 'relative',
        boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04)'
      }}>
        {/* Close Button */}
        <button
          onClick={onClose}
          style={{
            position: 'absolute',
            top: '16px',
            right: '16px',
            background: 'none',
            border: 'none',
            fontSize: '24px',
            cursor: 'pointer',
            color: '#6b7280',
            padding: '4px',
            borderRadius: '4px',
            transition: 'background-color 0.2s'
          }}
          onMouseEnter={(e) => e.target.style.backgroundColor = '#f3f4f6'}
          onMouseLeave={(e) => e.target.style.backgroundColor = 'transparent'}
        >
          <FiX />
        </button>

        {/* Header */}
        <div style={{ textAlign: 'center', marginBottom: '32px' }}>
          <div style={{
            backgroundColor: '#3b82f6',
            borderRadius: '50%',
            width: '64px',
            height: '64px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 16px auto'
          }}>
            <FiCreditCard size={32} color="white" />
          </div>
          <h2 style={{ margin: '0 0 8px 0', fontSize: '24px', fontWeight: '600', color: '#1f2937' }}>
            Upgrade to {selectedPlan?.plan_type?.toLowerCase() === 'free' ? 'Hobby' : selectedPlan?.plan_type?.charAt(0).toUpperCase() + selectedPlan?.plan_type?.slice(1)} Plan
          </h2>
          <p style={{ margin: 0, color: '#6b7280', fontSize: '16px' }}>
            Unlock more features and higher limits
          </p>
        </div>

        {/* Plan Details */}
        {selectedPlan && (
          <div style={{ marginBottom: '32px' }}>
            <div style={{
              backgroundColor: '#f8fafc',
              border: '2px solid #e2e8f0',
              borderRadius: '12px',
              padding: '24px',
              textAlign: 'center'
            }}>
              <div style={{ fontSize: '32px', fontWeight: '700', color: '#1f2937', marginBottom: '8px' }}>
                {selectedPlan.plan_type?.toLowerCase() === 'free' ? 'Free' : `$${selectedPlan.price_monthly}/mo.`}
              </div>
              {selectedPlan.price_yearly > 0 && (
                <div style={{ fontSize: '16px', color: '#6b7280', marginBottom: '16px' }}>
                  ${selectedPlan.price_yearly}/yr.
                </div>
              )}
              
              <div style={{ marginBottom: '20px' }}>
                <div style={{ fontSize: '14px', color: '#6b7280', marginBottom: '4px' }}>Credits per month</div>
                <div style={{ fontSize: '18px', fontWeight: '600', color: '#1f2937' }}>
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

              <div style={{ textAlign: 'left' }}>
                <h4 style={{ margin: '0 0 12px 0', fontSize: '16px', fontWeight: '600', color: '#374151' }}>
                  Features included:
                </h4>
                <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
                  {selectedPlan.features.map((feature, index) => {
                    // Transform feature text to fix "per run" to "per month"
                    const transformedFeature = feature
                      .replace(/records\/run/g, 'records/month')
                      .replace(/per run/g, 'per month');
                    
                    return (
                      <li key={index} style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        padding: '6px 0',
                        color: '#6b7280',
                        fontSize: '14px'
                      }}>
                        <FiCheck size={16} color="#10b981" />
                        {transformedFeature}
                      </li>
                    );
                  })}
                </ul>
              </div>
            </div>
          </div>
        )}

        {/* Billing Cycle Selector */}
        <div style={{ marginBottom: '24px' }}>
          <h4 style={{ margin: '0 0 12px 0', fontSize: '16px', fontWeight: '600', color: '#374151' }}>
            Choose Billing Cycle:
          </h4>
          <div style={{ display: 'flex', gap: '12px' }}>
            <label style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              cursor: 'pointer',
              padding: '12px 16px',
              border: `2px solid ${billingCycle === 'monthly' ? '#3b82f6' : '#e5e7eb'}`,
              borderRadius: '8px',
              backgroundColor: billingCycle === 'monthly' ? '#eff6ff' : 'white',
              flex: 1,
              justifyContent: 'center'
            }}>
              <input
                type="radio"
                name="billingCycle"
                value="monthly"
                checked={billingCycle === 'monthly'}
                onChange={(e) => setBillingCycle(e.target.value)}
                style={{ display: 'none' }}
              />
              <span style={{ fontWeight: '500', color: billingCycle === 'monthly' ? '#3b82f6' : '#6b7280' }}>
                Monthly ({selectedPlan?.plan_type?.toLowerCase() === 'free' ? 'Free' : `$${selectedPlan?.price_monthly}/mo.`})
              </span>
            </label>
            
            <label style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              cursor: 'pointer',
              padding: '12px 16px',
              border: `2px solid ${billingCycle === 'yearly' ? '#3b82f6' : '#e5e7eb'}`,
              borderRadius: '8px',
              backgroundColor: billingCycle === 'yearly' ? '#eff6ff' : 'white',
              flex: 1,
              justifyContent: 'center'
            }}>
              <input
                type="radio"
                name="billingCycle"
                value="yearly"
                checked={billingCycle === 'yearly'}
                onChange={(e) => setBillingCycle(e.target.value)}
                style={{ display: 'none' }}
              />
              <span style={{ fontWeight: '500', color: billingCycle === 'yearly' ? '#3b82f6' : '#6b7280' }}>
                Yearly (${selectedPlan?.price_yearly}/yr.)
              </span>
            </label>
          </div>
        </div>

        {/* Error Message */}
        {error && (
          <div style={{
            backgroundColor: '#fef2f2',
            border: '1px solid #fecaca',
            borderRadius: '8px',
            padding: '12px 16px',
            marginBottom: '24px',
            color: '#dc2626',
            fontSize: '14px'
          }}>
            {error}
          </div>
        )}

        {/* Action Buttons */}
        <div style={{ display: 'flex', gap: '12px' }}>
          <button
            onClick={onClose}
            disabled={isProcessing}
            style={{
              flex: 1,
              backgroundColor: '#f3f4f6',
              color: '#374151',
              border: 'none',
              padding: '14px 24px',
              borderRadius: '8px',
              fontSize: '16px',
              fontWeight: '500',
              cursor: isProcessing ? 'not-allowed' : 'pointer',
              transition: 'background-color 0.2s',
              opacity: isProcessing ? 0.6 : 1
            }}
            onMouseEnter={(e) => !isProcessing && (e.target.style.backgroundColor = '#e5e7eb')}
            onMouseLeave={(e) => !isProcessing && (e.target.style.backgroundColor = '#f3f4f6')}
          >
            Cancel
          </button>
          <button
            onClick={handleUpgrade}
            disabled={isProcessing}
            style={{
              flex: 2,
              backgroundColor: '#3b82f6',
              color: 'white',
              border: 'none',
              padding: '14px 24px',
              borderRadius: '8px',
              fontSize: '16px',
              fontWeight: '600',
              cursor: isProcessing ? 'not-allowed' : 'pointer',
              transition: 'background-color 0.2s',
              opacity: isProcessing ? 0.6 : 1
            }}
            onMouseEnter={(e) => !isProcessing && (e.target.style.backgroundColor = '#2563eb')}
            onMouseLeave={(e) => !isProcessing && (e.target.style.backgroundColor = '#3b82f6')}
          >
            {isProcessing ? (
              <>
                <FiLoader className="animate-spin" style={{ marginRight: '8px' }} />
                Processing...
              </>
            ) : (
              <>
                <FiCreditCard style={{ marginRight: '8px' }} />
                Upgrade Now
              </>
            )}
          </button>
        </div>

        {/* Footer Note */}
        <div style={{ textAlign: 'center', marginTop: '24px', padding: '16px', backgroundColor: '#f9fafb', borderRadius: '8px' }}>
          <p style={{ margin: 0, fontSize: '14px', color: '#6b7280' }}>
            🔒 Secure payment processing with Stripe. You can cancel anytime.
          </p>
        </div>
      </div>
    </div>
  );
};

export default PlanUpgradeModal;
