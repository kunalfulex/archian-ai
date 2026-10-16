import React, { useState, useEffect, useRef } from 'react';

import api from './api';
import { handleStripeCheckoutResponse, handleStripeCheckoutError } from './utils/stripeCheckout';
import './PlanManagementPage.css';
import PlanUpgradeModal from './components/PlanUpgradeModal';
import AlertModal from './components/AlertModal';
import { SUPPORT_EMAIL } from './config/site';

const PlanManagementPage = () => {
  const [currentPlan, setCurrentPlan] = useState(null);
  const [availablePlans, setAvailablePlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [upgrading, setUpgrading] = useState(false);
  const [showUpgradeModal, setShowUpgradeModal] = useState(false);
  const [selectedPlan, setSelectedPlan] = useState(null);
  const [showBuyCreditsModal, setShowBuyCreditsModal] = useState(false);
  const [buyingPack, setBuyingPack] = useState(null);
  const [customCredits, setCustomCredits] = useState(1000);
  const MIN_CREDITS = 1000;
  const hasInitialized = useRef(false); // Use ref to prevent duplicate calls
  
  // Alert modal state
  const [alertModal, setAlertModal] = useState({ isOpen: false, title: '', message: '', type: 'error' });
  
  // Helper function to show alerts
  const showAlert = (title, message, type = 'error') => {
    setAlertModal({ isOpen: true, title, message, type });
  };
  
  const closeAlert = () => {
    setAlertModal({ isOpen: false, title: '', message: '', type: 'error' });
  };

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
      setLoading(true);
      setError(null);

      // Fetch current plan
      const planResponse = await api.get('/user-plan');
      if (planResponse.data.status === 'success') {
        setCurrentPlan(planResponse.data.plan);
      }

      // Fetch available plans for comparison
      const plansResponse = await api.get('/plan-comparison');
      if (plansResponse.data.status === 'success') {
        setAvailablePlans(plansResponse.data.plans);
      }
    } catch (err) {
      console.error('Error fetching plan data:', err);
      setError('Failed to load plan information. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleUpgrade = async (planType) => {
    const plan = availablePlans.find(p => p.plan_type === planType);
    if (plan) {
      setSelectedPlan(plan);
      setShowUpgradeModal(true);
    }
  };

  const handlePayAsYouGoClick = async () => {
    const isPayg = currentPlan?.plan_type === 'pay_as_you_go';
    if (isPayg) {
      setShowBuyCreditsModal(true);
      return;
    }
    try {
      const res = await api.post('/user-plan/pay-as-you-go');
      if (res.data.status === 'success') {
        setCurrentPlan(res.data.plan);
        setShowBuyCreditsModal(true);
      } else {
        showAlert('Error', res.data.message || 'Could not switch to Pay as you go.');
      }
    } catch (err) {
      const msg = err.response?.data?.detail || err.response?.data?.message || err.message || 'Failed to switch to Pay as you go.';
      showAlert('Error', msg);
    }
  };

  const handleSubscribeCredits = async () => {
    const credits = Number(customCredits) || 0;
    if (credits < MIN_CREDITS) {
      return;
    }
    try {
      setBuyingPack(credits);
      const res = await api.post('/stripe/create-credit-pack-checkout', { credits });
      if (!handleStripeCheckoutResponse(res)) {
        showAlert('Error', res.data.message || 'Could not start checkout.');
      }
    } catch (err) {
      const { message, redirected } = handleStripeCheckoutError(err);
      if (!redirected) {
        showAlert('Error', message);
      }
    } finally {
      setBuyingPack(null);
    }
  };

  const setCreditsFromQuickSelect = (value) => {
    setCustomCredits(Math.max(MIN_CREDITS, value));
  };

  const processUpgrade = async (plan) => {
    try {
      setUpgrading(true);
      // TODO: Implement Stripe checkout here
      
      // For now, show a message
      showAlert('Upgrade Coming Soon', `Upgrade to ${plan.plan_type} plan - Stripe integration coming soon!`, 'info');
    } catch (err) {
      console.error('Error during upgrade:', err);
      setError('Failed to process upgrade. Please try again.');
    } finally {
      setUpgrading(false);
    }
  };



  if (loading) {
    return (
      <div className="plan-management-page">
        <div className="loading-container">
          <div className="loading-spinner"></div>
          <p>Loading plan information...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="plan-management-page">
        <div className="error-container">
          <h2>❌ Error</h2>
          <p>{error}</p>
          <button onClick={fetchPlanData} className="retry-button">
            Try Again
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="plan-management-page">
      <div className="page-header">
        <h1>Plan Management</h1>
        <p>Manage your subscription and compare plans</p>
      </div>

      {/* Plan Comparison Section */}
      <div className="plan-comparison-section">
        <h2>Available Plans</h2>
        <div className="plans-grid">
          {availablePlans
            .sort((a, b) => {
              // Move Enterprise plan to the end
              if (a.plan_type.toLowerCase() === 'enterprise') return 1;
              if (b.plan_type.toLowerCase() === 'enterprise') return -1;
              return 0;
            })
            .map((plan) => (
            <div 
              key={plan.plan_type} 
              className={`plan-card ${currentPlan?.plan_type === plan.plan_type ? 'current-plan' : ''}`}
            >
              <div className="plan-header">
                <h3>{plan.plan_type?.toLowerCase() === 'free' ? 'Hobby' : `${plan.plan_type.charAt(0).toUpperCase() + plan.plan_type.slice(1)}`}</h3>
                {currentPlan?.plan_type === plan.plan_type && (
                  <span className="current-badge">
                    Current Plan
                    {currentPlan?.billing_interval === 'annual' && ' · Billed annually'}
                    {currentPlan?.billing_interval === 'monthly' && ' · Billed monthly'}
                  </span>
                )}
              </div>

              <div className="plan-pricing">
                {plan.plan_type.toLowerCase() === 'enterprise' ? (
                  <span className="monthly-price">Contact for pricing</span>
                ) : plan.plan_type?.toLowerCase() === 'free' ? (
                  <span className="monthly-price">Free</span>
                ) : (
                  <>
                    <span className="monthly-price">${plan.price_monthly}/mo.</span>
                    {plan.price_yearly > 0 && (
                      <span className="yearly-price">${plan.price_yearly}/yr.</span>
                    )}
                  </>
                )}
              </div>

              <div className="plan-limits">
                <div className="limit-item">
                  <span className="limit-label">
                    {plan.plan_type?.toLowerCase() === 'free' ? 'Credits:' : 'Credits per month:'}
                  </span>
                  <span className="limit-value">
                    {plan.credits_per_month == null
                      ? '—'
                      : plan.credits_per_month === -1
                        ? (plan.plan_type?.toLowerCase() === 'enterprise' ? 'Custom' : 'Unlimited')
                        : plan.credits_per_month === 0 && plan.plan_type?.toLowerCase() === 'free'
                          ? '500'
                          : plan.credits_per_month === 0
                            ? '—'
                            : Number(plan.credits_per_month).toLocaleString()}
                  </span>
                </div>
              </div>

              <div className="plan-features">
                <h4>Features:</h4>
                <ul>
                  {plan.features.map((feature, index) => {
                    // Transform feature text to fix "per run" to "per month"
                    const transformedFeature = feature
                      .replace(/records\/run/g, 'records/month')
                      .replace(/per run/g, 'per month');
                    
                    return (
                      <li key={index}>✓ {transformedFeature}</li>
                    );
                  })}
                </ul>
              </div>

              <div 
                className={`plan-actions ${plan.plan_type.toLowerCase() === 'free' ? 'free-plan-actions' : ''}`}
              >
                {currentPlan?.plan_type === plan.plan_type ? (
                  <button className="current-plan-btn" disabled>
                    Current Plan
                  </button>
                ) : plan.plan_type.toLowerCase() === 'enterprise' ? (
                  <a 
                    href={`mailto:${SUPPORT_EMAIL}?subject=Enterprise Plan Inquiry`}
                    className="contact-btn"
                  >
                    Contact Us
                  </a>
                ) : (
                  <button 
                    className={`upgrade-btn primary`}
                    onClick={() => handleUpgrade(plan.plan_type)}
                    disabled={upgrading}
                  >
                    {upgrading ? 'Processing...' : `Upgrade to ${plan.plan_type?.toLowerCase() === 'free' ? 'Hobby' : plan.plan_type.charAt(0).toUpperCase() + plan.plan_type.slice(1)}`}
                  </button>
                )}
              </div>
            </div>
          ))}

          {/* Pay as you go card */}
          <div className="plan-card pay-as-you-go-card">
            <div className="plan-header">
              <h3>Pay as you go</h3>
            </div>
            <div className="plan-pricing">
              <span className="monthly-price">1 credit = $0.01</span>
            </div>
            <div className="plan-limits">
              <div className="limit-item">
                <span className="limit-label">Credits:</span>
                <span className="limit-value">As needed</span>
              </div>
            </div>
            <div className="plan-features">
              <h4>Features:</h4>
              <ul>
                <li>✓ Pay only for what you use</li>
                <li>✓ Files up to 10 MB</li>
                <li>✓ Subscribe to a credit pack monthly</li>
              </ul>
            </div>
            <div className="plan-actions pay-as-you-go-actions">
              <button
                type="button"
                className="upgrade-btn primary"
                onClick={handlePayAsYouGoClick}
              >
                Start Pay as you go
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* How credits work */}
      <div className="credits-how-section">
        <h2>How credits work</h2>
        <p className="credits-how-intro">
          Credits are used only for migration and metadata deploy. <strong>1 credit = $0.01.</strong> Extraction, cleanup, transformation, mapping, schema analysis, architect chat, spreadsheet rename, and rollback are included. Fixed plans include a monthly allowance; pay as you go uses monthly credit packs.
        </p>

        <div className="credits-categories">
          <div className="credits-category">
            <h4 className="credits-category-title">Migration</h4>
            <ul className="credits-list">
              <li className="credits-list-item">
                <span className="credits-action">1 record migrated</span>
                <span className="credits-badge">1 credit</span>
              </li>
              <li className="credits-list-item">
                <span className="credits-action">1 file attachment</span>
                <span className="credits-badge">5 credits</span>
              </li>
            </ul>
          </div>

          <div className="credits-category">
            <h4 className="credits-category-title">Metadata &amp; updates</h4>
            <ul className="credits-list">
              <li className="credits-list-item">
                <span className="credits-action">Metadata deploy</span>
                <span className="credits-badge credits-badge-formula">5 credits base + 1 credit per field (Salesforce org to org)</span>
              </li>
            </ul>
          </div>

          <div className="credits-category credits-category-included">
            <h4 className="credits-category-title">Included</h4>
            <ul className="credits-list">
              <li className="credits-list-item">
                <span className="credits-action">Extraction, cleanup, transformation, mapping build</span>
                <span className="credits-badge credits-badge-included">Included</span>
              </li>
              <li className="credits-list-item">
                <span className="credits-action">Schema analysis, architect chat, rollback</span>
                <span className="credits-badge credits-badge-included">Included</span>
              </li>
              <li className="credits-list-item">
                <span className="credits-action">Keep relationship intact</span>
                <span className="credits-badge credits-badge-included">Included</span>
              </li>
            </ul>
          </div>
        </div>
      </div>



      {/* Plan Upgrade Modal */}
      <PlanUpgradeModal
        isOpen={showUpgradeModal}
        onClose={() => setShowUpgradeModal(false)}
        selectedPlan={selectedPlan}
        onUpgrade={processUpgrade}
      />

      {/* Buy credits modal (PAYG) */}
      {showBuyCreditsModal && (
        <div className="modal-overlay" onClick={() => setShowBuyCreditsModal(false)}>
          <div className="modal-content buy-credits-modal" onClick={e => e.stopPropagation()}>
            <h3>Buy credits</h3>
            <p className="modal-description">1 credit = $0.01. Enter credits per month (minimum {MIN_CREDITS.toLocaleString()}); you get these credits each month.</p>
            <div className="buy-credits-custom">
              <label className="buy-credits-label">Credits per month</label>
              <input
                type="number"
                min={0}
                value={customCredits}
                onChange={(e) => {
                  const v = e.target.value;
                  setCustomCredits(v === '' ? '' : Math.max(0, parseInt(v, 10) || 0));
                }}
                className="buy-credits-input"
              />
              {(customCredits !== '' && Number(customCredits) < MIN_CREDITS) && (
                <p className="buy-credits-min-error">Minimum {MIN_CREDITS.toLocaleString()} credits</p>
              )}
              <div className="buy-credits-quick-select">
                {[1000, 2200, 6000].map((n) => (
                  <button
                    key={n}
                    type="button"
                    className={`quick-select-chip ${customCredits === n ? 'active' : ''}`}
                    onClick={() => setCreditsFromQuickSelect(n)}
                  >
                    {n.toLocaleString()}
                  </button>
                ))}
              </div>
              <p className="buy-credits-price">
                ${((Number(customCredits) || 0) * 0.01).toFixed(2)}/mo.
              </p>
              <button
                type="button"
                className="buy-credits-subscribe-btn"
                onClick={handleSubscribeCredits}
                disabled={buyingPack != null}
              >
                {buyingPack != null ? '...' : 'Subscribe'}
              </button>
            </div>
            <button type="button" className="modal-close-btn" onClick={() => setShowBuyCreditsModal(false)}>Close</button>
          </div>
        </div>
      )}

      {/* Alert Modal */}
      <AlertModal
        isOpen={alertModal.isOpen}
        onClose={closeAlert}
        title={alertModal.title}
        message={alertModal.message}
        type={alertModal.type}
      />
    </div>
  );
};

export default PlanManagementPage;
