import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { FiCheckCircle, FiCreditCard, FiHome, FiArrowLeft } from 'react-icons/fi';
import api from './api';
import './PaymentSuccessPage.css';
import { SUPPORT_EMAIL } from './config/site';

const PaymentSuccessPage = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [planDetails, setPlanDetails] = useState(null);
  const [hasVerified, setHasVerified] = useState(false); // Prevent duplicate verification
  
  const sessionId = searchParams.get('session_id');
  const paymentType = searchParams.get('type'); // 'credit_pack' | subscription

  const verifyPayment = useCallback(async () => {
    try {
      // Verify the payment session with Stripe
      const response = await api.get(`/verify-payment/${sessionId}`);
      
      if (response.data.status === 'success') {
        setPlanDetails(response.data.data.plan);
      } else {
        setError(response.data.message || 'Failed to verify payment');
      }
    } catch (err) {
      console.error('Error verifying payment:', err);
      setError('Failed to verify payment details');
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    if (sessionId && !hasVerified) {
      // Verify the payment was successful (only once)
      setHasVerified(true);
      verifyPayment();
    } else if (!sessionId) {
      setError('No session ID found');
      setLoading(false);
    }
  }, [sessionId, hasVerified, verifyPayment]);

  if (loading) {
    return (
      <div className="payment-success-page">
        <div className="loading-container">
          <div className="loading-spinner"></div>
          <p>Verifying your payment...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="payment-success-page">
        <div className="error-container">
          <div className="error-icon">❌</div>
          <h2>Payment Verification Failed</h2>
          <p>{error}</p>
          <button 
            onClick={() => navigate('/plans')} 
            className="retry-btn"
            style={{ 
              display: 'flex', 
              alignItems: 'center', 
              justifyContent: 'center',
              width: '48px',
              height: '48px',
              borderRadius: '50%',
              padding: '0',
              minWidth: '48px'
            }}
            title="Go Back to Plans"
          >
            <FiArrowLeft size={20} />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="payment-success-page">
      <div className="success-container">
        {/* Success Icon */}
        <div className="success-icon">
          <FiCheckCircle size={80} />
        </div>

        {/* Success Message */}
        <h1>Payment Successful! 🎉</h1>
        <p className="success-subtitle">
          {paymentType === 'credit_pack'
            ? 'Credits have been added to your account.'
            : `Welcome to your new ${planDetails?.plan_type?.toLowerCase() === 'free' ? 'Hobby' : planDetails?.plan_type?.charAt(0).toUpperCase() + planDetails?.plan_type?.slice(1)} plan!`}
        </p>

        {/* Plan Details: unified credit-based layout for all plans (PAYG, credit pack, standard) */}
        {planDetails && (
          <div className="plan-details">
            <h3>Your New Plan Details</h3>
            <div className="plan-info-grid">
              <div className="plan-info-item">
                <span className="label">Plan Type:</span>
                <span className="value">
                  {planDetails?.plan_type?.toLowerCase() === 'free' ? 'Hobby' : (planDetails.plan_type || '')
                    .replace(/_/g, ' ')
                    .replace(/^\w/, (c) => c.toUpperCase())}
                </span>
              </div>
              <div className="plan-info-item">
                <span className="label">Credits:</span>
                <span className="value">
                  {planDetails.credits_per_month == null
                    ? '—'
                    : planDetails.credits_per_month === -1
                      ? (planDetails.plan_type?.toLowerCase() === 'enterprise' ? 'Custom' : 'Unlimited')
                      : planDetails.credits_per_month > 0
                        ? `${Number(planDetails.credits_per_month).toLocaleString()} per month`
                        : 'Usage-based'}
                </span>
              </div>
              <div className="plan-info-item">
                <span className="label">Current balance:</span>
                <span className="value">
                  {planDetails.credit_balance != null ? Number(planDetails.credit_balance).toLocaleString() : '—'} credits
                </span>
              </div>
              {planDetails.amount_cents != null && planDetails.amount_cents > 0 && (
                <div className="plan-info-item">
                  <span className="label">Monthly amount:</span>
                  <span className="value">${(planDetails.amount_cents / 100).toFixed(2)}/mo.</span>
                </div>
              )}
              <div className="plan-info-item">
                <span className="label">File size limit:</span>
                <span className="value">{planDetails.max_file_size_mb != null ? `${planDetails.max_file_size_mb}MB` : 'N/A'}</span>
              </div>
              <div className="plan-info-item">
                <span className="label">Status:</span>
                <span className="value status-active">{planDetails.status ?? 'active'}</span>
              </div>
            </div>
          </div>
        )}

        {/* Action Buttons */}
        <div className="action-buttons">
          <button 
            onClick={() => navigate('/dashboard')} 
            className="primary-btn"
          >
            <FiHome size={20} />
            Go to Dashboard
          </button>
          
          <button 
            onClick={() => navigate('/plans')} 
            className="secondary-btn"
          >
            <FiCreditCard size={20} />
            Manage Plan
          </button>
        </div>

        {/* Next Steps */}
        <div className="next-steps">
          <h3>What's Next?</h3>
          <div className="steps-grid">
            <div className="step-item">
              <div className="step-number">1</div>
              <div className="step-content">
                <h4>Start Using Your Credits</h4>
                <p>
                  {planDetails?.credit_balance != null
                    ? `Use your ${Number(planDetails.credit_balance).toLocaleString()} credits for migrations, extraction, cleanup, and more.`
                    : planDetails?.credits_per_month != null && planDetails.credits_per_month !== -1 && planDetails.credits_per_month > 0
                      ? `You have ${Number(planDetails.credits_per_month).toLocaleString()} credits per month for migrations, extraction, and more.`
                      : planDetails?.credits_per_month === -1
                        ? 'Use your unlimited credits for migrations, extraction, cleanup, and more.'
                        : 'Use your credits for migrations, extraction, cleanup, and more.'}
                </p>
              </div>
            </div>
            <div className="step-item">
              <div className="step-number">2</div>
              <div className="step-content">
                <h4>Upload Files & Run Jobs</h4>
                <p>File size limit: {planDetails?.max_file_size_mb != null ? `${planDetails.max_file_size_mb}MB` : 'N/A'}</p>
              </div>
            </div>
            <div className="step-item">
              <div className="step-number">3</div>
              <div className="step-content">
                <h4>Access Premium Features</h4>
                <p>Enjoy all the features included in your plan.</p>
              </div>
            </div>
          </div>
        </div>

        {/* Support Info */}
        <div className="support-info">
          <p>
            Need help? Contact our support team at{' '}
            <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
          </p>
        </div>
      </div>
    </div>
  );
};

export default PaymentSuccessPage;
