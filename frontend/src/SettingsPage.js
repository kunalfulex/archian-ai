import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { FiUser, FiShield, FiBell, FiCreditCard, FiTrash2, FiEdit2, FiSave, FiX, FiCheck, FiAlertTriangle, FiDownload, FiDatabase, FiExternalLink, FiActivity } from 'react-icons/fi';
import api from './api';
import './SettingsPage.css';
import ChangePasswordModal from './components/ChangePasswordModal';
import LoginHistoryModal from './components/LoginHistoryModal';
import DeleteAccountModal from './components/DeleteAccountModal';
import SearchableTimezoneSelect from './components/SearchableTimezoneSelect';
import { SUPPORT_EMAIL, privacyUrl, termsUrl } from './config/site';
import { useFeatures } from './hooks/useFeatures';

const SettingsPage = () => {
  const navigate = useNavigate();
  const { billing, loading: featuresLoading } = useFeatures();
  const [activeTab, setActiveTab] = useState('profile');
  const [isEditing, setIsEditing] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const [timezones, setTimezones] = useState({});
  const [showChangePasswordModal, setShowChangePasswordModal] = useState(false);
  const [showLoginHistoryModal, setShowLoginHistoryModal] = useState(false);
  const [showDeleteAccountModal, setShowDeleteAccountModal] = useState(false);
  const [isDeletingAccount, setIsDeletingAccount] = useState(false);
  
  // User plan data
  const [userPlan, setUserPlan] = useState(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [portalLoading, setPortalLoading] = useState(false);
  const [invoices, setInvoices] = useState([]);
  const [invoicesLoading, setInvoicesLoading] = useState(false);
  const [invoiceMonth, setInvoiceMonth] = useState('');
  const [usageSummary, setUsageSummary] = useState(null);
  const [usageTransactions, setUsageTransactions] = useState([]);
  const [usageLoading, setUsageLoading] = useState(false);
  const [usageMonth, setUsageMonth] = useState(() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  });
  
  // Export modal data
  const [showExportModal, setShowExportModal] = useState(false);
  const [exportStartDate, setExportStartDate] = useState('');
  const [exportEndDate, setExportEndDate] = useState('');
  const [isExporting, setIsExporting] = useState(false);

  // Data retention (GET/PATCH /user/data-retention)
  const [dataRetentionSettings, setDataRetentionSettings] = useState(null);
  const [dataRetentionLoading, setDataRetentionLoading] = useState(false);
  const [showDataRetentionModal, setShowDataRetentionModal] = useState(false);
  const [dataRetentionDraft, setDataRetentionDraft] = useState(30);
  const [dataRetentionModalError, setDataRetentionModalError] = useState(null);
  const [isSavingRetention, setIsSavingRetention] = useState(false);
  
  // Profile form data
  const [formData, setFormData] = useState({
    firstName: '',
    lastName: '',
    email: '',
    company: '',
    phone: '',
    timezone: 'Europe/London|London, UK',
    notifications: {
      email: true, // Always enabled
      push: false,
      sms: false
    }
  });
  const hasInitialized = useRef(false); // Use ref to prevent duplicate calls


  // Fetch user profile and timezones on component mount
  useEffect(() => {
    // Prevent multiple executions using ref
    if (hasInitialized.current) {
      return;
    }
    hasInitialized.current = true;

    fetchUserProfile();
    fetchTimezones();
  }, []);

  useEffect(() => {
    if (featuresLoading || !billing) return;
    fetchUserPlan();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fetch once billing is known to be enabled
  }, [billing, featuresLoading]);

  const fetchDataRetention = async () => {
    try {
      setDataRetentionLoading(true);
      const response = await api.get('/user/data-retention');
      if (response.data?.status === 'success' && response.data?.data) {
        const d = response.data.data;
        setDataRetentionSettings(d);
        setDataRetentionDraft(
          Number(d.effective_data_retention_days ?? d.data_retention_days ?? 30)
        );
      }
    } catch (err) {
      console.error('Error fetching data retention:', err);
      setDataRetentionSettings(null);
    } finally {
      setDataRetentionLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab !== 'data-privacy') return;
    fetchDataRetention();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refresh when switching to this tab only
  }, [activeTab]);

  const handleOpenDataRetentionModal = () => {
    setDataRetentionModalError(null);
    if (dataRetentionSettings) {
      setDataRetentionDraft(
        Number(
          dataRetentionSettings.effective_data_retention_days ??
            dataRetentionSettings.data_retention_days ??
            30
        )
      );
    }
    setShowDataRetentionModal(true);
  };

  const handleCloseDataRetentionModal = () => {
    setShowDataRetentionModal(false);
    setDataRetentionModalError(null);
  };

  const handleSaveDataRetention = async () => {
    const maxDays = dataRetentionSettings?.max_allowed_days ?? 30;
    const days = parseInt(String(dataRetentionDraft), 10);
    if (Number.isNaN(days) || days < 1 || days > maxDays) {
      setDataRetentionModalError(`Enter a whole number between 1 and ${maxDays}.`);
      return;
    }
    try {
      setIsSavingRetention(true);
      setDataRetentionModalError(null);
      const response = await api.patch('/user/data-retention', {
        data_retention_days: days,
      });
      if (response.data?.status === 'success' && response.data?.data) {
        setDataRetentionSettings(response.data.data);
        setSuccess('Data retention updated.');
        setTimeout(() => setSuccess(null), 4000);
        handleCloseDataRetentionModal();
      } else {
        setDataRetentionModalError(response.data?.message || 'Could not save settings.');
      }
    } catch (err) {
      const msg =
        err.response?.data?.message ||
        err.response?.data?.detail ||
        err.message ||
        'Could not save settings.';
      setDataRetentionModalError(typeof msg === 'string' ? msg : 'Could not save settings.');
    } finally {
      setIsSavingRetention(false);
    }
  };

  const fetchUserProfile = async () => {
    try {
      setIsLoading(true);
      setError(null);
      
      const response = await api.get('/profile');
      
      if (response.data.status === 'success') {
        const profile = response.data.data;
        setFormData({
          firstName: profile.first_name || '',
          lastName: profile.last_name || '',
          email: profile.email || '',
          company: profile.company || '',
          phone: profile.phone || '',
          timezone: profile.timezone || 'Europe/London|London, UK',
          notifications: {
            email: true, // Always enabled
            push: false,
            sms: false
          }
        });
      } else {
        setError('Failed to load profile data');
      }
    } catch (err) {
      console.error('Error fetching profile:', err);
      setError('Failed to load profile data. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const fetchTimezones = async () => {
    try {
      const response = await api.get('/profile/timezones');
      if (response.data.status === 'success') {
        setTimezones(response.data.data);
      }
    } catch (err) {
      console.error('Error fetching timezones:', err);
      // Don't show error for timezones, just use default
    }
  };

  const fetchUserPlan = async () => {
    try {
      setPlanLoading(true);
      const response = await api.get('/user-plan');
      if (response.data.status === 'success') {
        setUserPlan(response.data.plan);
      } else {
        console.error('Failed to fetch user plan:', response.data.message);
      }
    } catch (err) {
      console.error('Error fetching user plan:', err);
    } finally {
      setPlanLoading(false);
    }
  };

  const handleInputChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: value
    }));
    
    // Clear any previous errors/success messages
    setError(null);
    setSuccess(null);
  };


  const handleChangePassword = () => {
    setShowChangePasswordModal(true);
  };

  const handlePasswordChangeSuccess = () => {
    setSuccess('Password changed successfully!');
    // Clear success message after 3 seconds
    setTimeout(() => setSuccess(null), 3000);
  };

  const handleDeleteAccount = async (reason) => {
    try {
      setIsDeletingAccount(true);
      setError(null);
      
      const response = await api.post('/api/delete-account', { reason });
      
      if (response.data.success) {
        setSuccess('Account deleted successfully. You can reactivate within 30 days by signing up again.');
        // Redirect to logout or home page after a delay
        setTimeout(() => {
          // Clear local storage and redirect
          localStorage.clear();
          window.location.href = '/';
        }, 3000);
      } else {
        setError(response.data.message || 'Failed to delete account');
      }
    } catch (err) {
      console.error('Error deleting account:', err);
      setError(err.response?.data?.detail || 'Failed to delete account. Please try again.');
    } finally {
      setIsDeletingAccount(false);
      setShowDeleteAccountModal(false);
    }
  };

  const handleViewLoginHistory = () => {
    setShowLoginHistoryModal(true);
  };

  // Data & Privacy handlers
  const handleDataDeletion = () => {
    window.open(`mailto:${SUPPORT_EMAIL}?subject=Data Deletion Request&body=Please delete my data and migration records. User ID: [Your User ID]`, '_blank');
  };

  const handleViewPrivacyPolicy = () => {
    window.open(privacyUrl, '_blank');
  };

  const handleViewTermsOfService = () => {
    window.open(termsUrl, '_blank');
  };

  const handleDataExport = () => {
    // Set default date range (last 30 days)
    const endDate = new Date();
    const startDate = new Date();
    startDate.setDate(startDate.getDate() - 30);
    
    setExportStartDate(startDate.toISOString().split('T')[0]);
    setExportEndDate(endDate.toISOString().split('T')[0]);
    setShowExportModal(true);
  };

  const handleExportSubmit = async () => {
    if (!exportStartDate || !exportEndDate) {
      setError('Please select both start and end dates');
      return;
    }

    if (new Date(exportStartDate) > new Date(exportEndDate)) {
      setError('Start date cannot be after end date');
      return;
    }

    try {
      setIsExporting(true);
      setError(null);

      await api.post('/export-migration-data', {
        start_date: exportStartDate,
        end_date: exportEndDate
      });

      setShowExportModal(false);
      setSuccess("Export is in progress. We'll email you when it's ready with a download link for your export report.");
    } catch (error) {
      console.error('Export error:', error);
      const msg = error.response?.data?.detail || error.message || 'Please try again.';
      setError(Array.isArray(msg) ? msg.join(' ') : msg);
    } finally {
      setIsExporting(false);
    }
  };

  const handleExportCancel = () => {
    setShowExportModal(false);
    setExportStartDate('');
    setExportEndDate('');
    setError(null);
    setIsExporting(false); // Reset export state when canceling
  };

  const handleExportReset = () => {
    setIsExporting(false);
    setError(null);
  };

  const handleSave = async () => {
    try {
      setIsSaving(true);
      setError(null);
      setSuccess(null);

      const profileData = {
        first_name: formData.firstName,
        last_name: formData.lastName,
        email: formData.email,
        company: formData.company,
        phone: formData.phone,
        timezone: formData.timezone
      };

      const response = await api.put('/profile', profileData);

      if (response.data.status === 'success') {
        setSuccess('Profile updated successfully!');
        setIsEditing(false);
        
        // Update form data with the response data to ensure consistency
        const updatedProfile = response.data.data;
        setFormData({
          firstName: updatedProfile.first_name || '',
          lastName: updatedProfile.last_name || '',
          email: updatedProfile.email || '',
          company: updatedProfile.company || '',
          phone: updatedProfile.phone || '',
          timezone: updatedProfile.timezone || 'UTC+0 (GMT)',
          notifications: {
            email: true,
            push: false,
            sms: false
          }
        });
      } else {
        setError(response.data.message || 'Failed to update profile');
      }
    } catch (err) {
      console.error('Error updating profile:', err);
      if (err.response?.data?.message) {
        setError(err.response.data.message);
      } else {
        setError('Failed to update profile. Please try again.');
      }
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancel = () => {
    // Reset form data to original values
    fetchUserProfile();
    setIsEditing(false);
    setError(null);
    setSuccess(null);
  };

  const renderProfileTab = () => (
    <div className="settings-section">
      <div className="section-header">
        <h3><FiUser /> Profile Information</h3>
        {!isLoading && !isEditing && (
          <button
            type="button"
            className="edit-btn"
            onClick={() => setIsEditing(true)}
          >
            <FiEdit2 />
            Edit Profile
          </button>
        )}
        {!isLoading && isEditing && (
          <div className="header-actions">
            <button
              type="button"
              className="cancel-btn"
              onClick={handleCancel}
            >
              <FiX />
              Cancel
            </button>
            <button
              type="button"
              className="save-btn"
              onClick={handleSave}
              disabled={isSaving}
            >
              {isSaving ? (
                <>
                  <div className="spinner-small"></div>
                  Saving...
                </>
              ) : (
                <>
                  <FiSave />
                  Save Changes
                </>
              )}
            </button>
          </div>
        )}
      </div>

      {isLoading ? (
        <div className="loading-state">
          <div className="spinner"></div>
          <p>Loading profile...</p>
        </div>
      ) : (
        <>
          <div className="profile-info">
            <div className="profile-header">
              <div className="profile-avatar">
                <div className="avatar-circle">
                  {formData.firstName && formData.lastName ? 
                    `${formData.firstName.charAt(0)}${formData.lastName.charAt(0)}` : 
                    'U'
                  }
                </div>
              </div>
              <div className="profile-details">
                <h4>{formData.firstName} {formData.lastName}</h4>
                <p>{formData.email}</p>
                {formData.company && <p className="company">{formData.company}</p>}
              </div>
            </div>

            <form className="profile-form" onSubmit={(e) => e.preventDefault()}>
              <div className="form-section">
                <h4 className="section-title">Personal Information</h4>
                <div className="form-grid">
                  <div className="form-group">
                    <label htmlFor="firstName">
                      First Name <span className="required">*</span>
                    </label>
                    <input
                      type="text"
                      id="firstName"
                      name="firstName"
                      value={formData.firstName}
                      onChange={handleInputChange}
                      disabled={!isEditing}
                      required
                    />
                  </div>
                  
                  <div className="form-group">
                    <label htmlFor="lastName">
                      Last Name <span className="required">*</span>
                    </label>
                    <input
                      type="text"
                      id="lastName"
                      name="lastName"
                      value={formData.lastName}
                      onChange={handleInputChange}
                      disabled={!isEditing}
                      required
                    />
                  </div>
                </div>

                <div className="form-group full-width">
                  <label htmlFor="email">
                    Email Address <span className="required">*</span>
                  </label>
                  <input
                    type="email"
                    id="email"
                    name="email"
                    value={formData.email}
                    onChange={handleInputChange}
                    disabled={!isEditing}
                    required
                  />
                </div>
              </div>

              <div className="form-section">
                <h4 className="section-title">Contact Information</h4>
                <div className="form-grid">
                  <div className="form-group">
                    <label htmlFor="company">Company</label>
                    <input
                      type="text"
                      id="company"
                      name="company"
                      value={formData.company}
                      onChange={handleInputChange}
                      disabled={!isEditing}
                      placeholder="Your company name"
                    />
                  </div>
                  
                  <div className="form-group">
                    <label htmlFor="phone">Phone Number</label>
                    <input
                      type="tel"
                      id="phone"
                      name="phone"
                      value={formData.phone}
                      onChange={handleInputChange}
                      disabled={!isEditing}
                      placeholder="+1 (555) 123-4567"
                    />
                  </div>
                </div>

                <div className="form-group full-width">
                  <label htmlFor="timezone">Timezone</label>
                  <SearchableTimezoneSelect
                    value={formData.timezone}
                    onChange={handleInputChange}
                    disabled={!isEditing}
                    timezones={timezones}
                  />
                </div>
              </div>


            </form>
          </div>
        </>
      )}
    </div>
  );

  const renderSecurityTab = () => (
    <div className="settings-section">
      <div className="section-header">
        <h3><FiShield /> Security & Privacy</h3>
      </div>
      
      <div className="security-options">
        <div className="security-item">
          <div className="security-info">
            <h4>Change Password</h4>
            <p>Update your account password for enhanced security</p>
          </div>
          <button className="action-btn" onClick={handleChangePassword}>Change</button>
        </div>
        
        <div className="security-item">
          <div className="security-info">
            <h4>Two-Factor Authentication</h4>
            <p>Add an extra layer of security to your account</p>
            <span className="status-badge enabled">Enabled</span>
          </div>
          {/* <button className="action-btn">Manage</button> */}
        </div>
        
        <div className="security-item">
          <div className="security-info">
            <h4>Login History</h4>
            <p>View recent login attempts and device information</p>
          </div>
          <button className="action-btn" onClick={handleViewLoginHistory}>View</button>
        </div>
        
        <div className="security-item">
          <div className="security-info">
            <h4>Data Export</h4>
            <p>Download a copy of your data and migration history</p>
          </div>
          <button
            className="action-btn"
            onClick={handleDataExport}
          >
            <FiDownload /> Export
          </button>
        </div>
      </div>
    </div>
  );

  const renderDataPrivacyTab = () => (
    <div className="settings-section">
      <div className="section-header">
        <h3><FiDatabase /> Data & Privacy</h3>
      </div>
      
      <div className="data-privacy-options">
        <div className="data-privacy-item">
          <div className="data-privacy-info">
            <h4>Data Retention</h4>
            <p>
              Choose how long we keep your <strong>extracted data</strong> and <strong>migrated results</strong>{' '}
              (the working copies we hold in memory for previews, downloads, and reruns) before removal.
            </p>
            {dataRetentionLoading ? (
              <span className="status-badge info">Loading…</span>
            ) : dataRetentionSettings ? (
              <>
                <span className="status-badge info">
                  {Number(dataRetentionSettings.effective_data_retention_days) === 1
                    ? '1 day'
                    : `${dataRetentionSettings.effective_data_retention_days} days`}{' '}
                  · max {dataRetentionSettings.max_allowed_days}{' '}
                  {dataRetentionSettings.max_allowed_days === 1 ? 'day' : 'days'} for your plan
                </span>
                {dataRetentionSettings.max_allowed_days > 30 && (
                  <p className="data-retention-enterprise-hint">
                    Your plan allows a longer retention window than standard accounts (1–30 days).
                  </p>
                )}
              </>
            ) : (
              <span className="status-badge warning">Could not load retention settings</span>
            )}
          </div>
          <button
            type="button"
            className="action-btn"
            disabled={dataRetentionLoading || !dataRetentionSettings}
            onClick={handleOpenDataRetentionModal}
          >
            Manage
          </button>
        </div>
        
        <div className="data-privacy-item">
          <div className="data-privacy-info">
            <h4>Data Deletion</h4>
            <p>Request deletion of specific data or entire migration records</p>
          </div>
          <button className="action-btn" onClick={handleDataDeletion}>
            Request Deletion
          </button>
        </div>
        
        <div className="data-privacy-item">
          <div className="data-privacy-info">
            <h4>Privacy Policy</h4>
            <p>Review our privacy policy and data handling practices</p>
          </div>
          <button className="action-btn" onClick={handleViewPrivacyPolicy}>
            View Policy
          </button>
        </div>
        
        <div className="data-privacy-item">
          <div className="data-privacy-info">
            <h4>Terms of Service</h4>
            <p>Review our terms of service and user agreement</p>
          </div>
          <button className="action-btn" onClick={handleViewTermsOfService}>
            View Terms
          </button>
        </div>
      </div>
    </div>
  );

  const renderNotificationsTab = () => (
    <div className="settings-section">
      <div className="section-header">
        <h3><FiBell /> Notifications</h3>
      </div>
      
      <div className="notification-settings">
        <div className="notification-item">
          <div className="notification-info">
            <h4>Email Notifications</h4>
            <p>Receive updates about your migrations and account (Always enabled)</p>
          </div>
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={true}
              disabled={true}
            />
            <span className="slider"></span>
          </label>
        </div>
        
        {/* <div className="notification-item">
          <div className="notification-info">
            <h4>Push Notifications</h4>
            <p>Get real-time alerts in your browser</p>
          </div>
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={formData.notifications.push}
              onChange={(e) => handleNotificationChange('push', e.target.checked)}
            />
            <span className="slider"></span>
          </label>
        </div>
        
        <div className="notification-item">
          <div className="notification-info">
            <h4>SMS Notifications</h4>
            <p>Receive text messages for important updates</p>
          </div>
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={formData.notifications.sms}
              onChange={(e) => handleNotificationChange('sms', e.target.checked)}
            />
            <span className="slider"></span>
          </label>
        </div> */}
      </div>
    </div>
  );

  const formatPlanDisplayName = (planType) => {
    if (!planType) return 'Plan';
    if (planType === 'pay_as_you_go') return 'Pay as you go';
    if (planType === 'free') return 'Hobby';
    return planType.charAt(0).toUpperCase() + planType.slice(1).replace(/_/g, ' ');
  };

  const fetchInvoices = async () => {
    if (!userPlan?.stripe_customer_id) return;
    try {
      setInvoicesLoading(true);
      const response = await api.get('/stripe/invoices', { params: invoiceMonth ? { month: invoiceMonth } : {} });
      // eslint-disable-next-line react-hooks/exhaustive-deps
      if (response.data?.status === 'success') {
        setInvoices(response.data.invoices || []);
      } else {
        setInvoices([]);
      }
    } catch (err) {
      console.error('Failed to fetch invoices:', err);
      setInvoices([]);
    } finally {
      setInvoicesLoading(false);
    }
  };

  useEffect(() => {
    if (userPlan?.stripe_customer_id && activeTab === 'billing') {
      fetchInvoices();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- tab/month driven refresh only
  }, [userPlan?.stripe_customer_id, invoiceMonth, activeTab]);

  const fetchUsage = async () => {
    try {
      setUsageLoading(true);
      const response = await api.get('/credit-usage', { params: usageMonth ? { month: usageMonth } : {} });
      if (response.data?.status === 'success') {
        setUsageSummary(response.data.summary || null);
        setUsageTransactions(response.data.transactions || []);
      } else {
        setUsageSummary(null);
        setUsageTransactions([]);
      }
    } catch (err) {
      console.error('Failed to fetch credit usage:', err);
      setUsageSummary(null);
      setUsageTransactions([]);
    } finally {
      setUsageLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'usage') {
      fetchUsage();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- tab/month driven refresh only
  }, [activeTab, usageMonth]);

  const handleManageSubscription = async () => {
    try {
      setPortalLoading(true);
      const response = await api.post('/stripe/create-portal-session', { return_path: '/settings' });
      if (response.data?.status === 'success' && response.data?.data?.url) {
        window.location.href = response.data.data.url;
      } else {
        setError(response.data?.message || 'Could not open subscription management');
      }
    } catch (err) {
      const msg = err.response?.data?.message || err.response?.data?.detail || err.message || 'Failed to open subscription management';
      setError(msg);
    } finally {
      setPortalLoading(false);
    }
  };

  const renderUsageTab = () => {
    const credits = usageSummary?.usage?.credits;
    const used = credits?.used ?? 0;
    const limit = credits?.limit;
    const remaining = credits?.remaining;
    const isUnlimited = limit === "unlimited";
    return (
      <div className="settings-section usage-section">
        <div className="section-header">
          <h3><FiActivity /> Credit usage</h3>
        </div>
        <p className="usage-subtitle">Usage across your billing period.</p>
        {usageLoading ? (
          <p className="usage-loading">Loading usage…</p>
        ) : (
          <>
            {usageSummary && (
              <div className="usage-summary-cards">
                <div className="usage-summary-card">
                  <span className="usage-summary-label">Credits used this month</span>
                  <span className="usage-summary-value">{Number(used).toLocaleString()}</span>
                </div>
                <div className="usage-summary-card">
                  <span className="usage-summary-label">Limit</span>
                  <span className="usage-summary-value">{isUnlimited ? 'Unlimited' : (limit != null ? Number(limit).toLocaleString() : '—')}</span>
                </div>
                <div className="usage-summary-card">
                  <span className="usage-summary-label">Remaining</span>
                  <span className="usage-summary-value">
                    {isUnlimited ? 'Unlimited' : (remaining != null && remaining !== 'unlimited' ? Number(remaining).toLocaleString() : (remaining ?? '—'))}
                  </span>
                </div>
              </div>
            )}
            <div className="usage-history-header">
              <h4 className="usage-history-title">Usage history</h4>
              <select
                className="usage-month-select"
                value={usageMonth}
                onChange={(e) => setUsageMonth(e.target.value)}
                aria-label="Select month"
              >
                {(() => {
                  const options = [];
                  const d = new Date();
                  for (let i = 0; i < 24; i++) {
                    const y = d.getFullYear();
                    const m = d.getMonth() + 1;
                    const value = `${y}-${String(m).padStart(2, '0')}`;
                    const label = d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
                    options.push(<option key={value} value={value}>{label}</option>);
                    d.setMonth(d.getMonth() - 1);
                  }
                  return options;
                })()}
              </select>
            </div>
            <div className="usage-table-wrap">
              <table
                className="usage-table"
                style={{ tableLayout: 'fixed', width: '100%' }}
              >
                <thead>
                  <tr>
                    <th style={{ width: 210, textAlign: 'left' }}>Date</th>
                    <th style={{ minWidth: 0 }}>Description</th>
                    <th style={{ width: 80 }}>Credits</th>
                  </tr>
                </thead>
                <tbody>
                  {usageTransactions.length === 0 ? (
                    <tr>
                      <td colSpan={3} className="usage-empty">No usage for this period.</td>
                    </tr>
                  ) : (
                    usageTransactions.map((tx) => (
                      <tr key={tx.id}>
                        <td
                          className="usage-date-cell"
                          style={{
                            width: 210,
                            minWidth: 210,
                            maxWidth: 210,
                            whiteSpace: 'nowrap',
                            boxSizing: 'border-box',
                            overflow: 'hidden',
                            textAlign: 'left',
                          }}
                        >
                          {tx.date ? new Date(tx.date).toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}
                        </td>
                        <td style={{ minWidth: 0, wordBreak: 'break-word' }}>
                          {tx.description || '—'}
                        </td>
                        <td className="usage-credits-cell" style={{ width: 80 }}>
                          {tx.amount_display ?? (tx.credits != null ? `-${tx.credits}` : '—')}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    );
  };

  const renderBillingTab = () => (
    <div className="settings-section billing-section">
      <div className="section-header">
        <h3><FiCreditCard /> Billing & Subscription</h3>
      </div>
      
      <div className="billing-info">
        {planLoading ? (
          <div className="current-plan-card">
            <p>Loading plan information...</p>
          </div>
        ) : userPlan ? (
          <div className={`current-plan-card ${userPlan.plan_type}`}>
            <div className="plan-header">
              <h4>Current Plan</h4>
              <span className={`plan-badge ${userPlan.plan_type}`}>
                {formatPlanDisplayName(userPlan.plan_type)} Plan
              </span>
            </div>
            <div className="plan-details plan-management remove-background-color">
              <p><strong>Status:</strong> {userPlan.status}</p>
              <p><strong>Next billing date:</strong>{' '}
                {userPlan.plan_type === 'free' ? 'Never (Hobby)' : 
                 userPlan.current_period_end
                   ? (userPlan.plan_type === 'pay_as_you_go'
                      ? new Date(userPlan.current_period_end).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) + ' (credit pack renewal)'
                      : new Date(userPlan.current_period_end).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }))
                   : 'N/A'}
              </p>
              {userPlan.plan_type === 'pay_as_you_go' ? (
                <>
                  <p><strong>Monthly credits:</strong>{' '}
                    {userPlan.credits_per_month != null && userPlan.credits_per_month !== -1 && userPlan.credits_per_month > 0
                      ? `${Number(userPlan.credits_per_month).toLocaleString()} credits/month`
                      : 'No credit pack'}
                  </p>
                  <p><strong>Monthly amount:</strong>{' '}
                    {userPlan.amount_cents != null && userPlan.amount_cents > 0
                      ? `$${(userPlan.amount_cents / 100).toFixed(2)}/mo.`
                      : userPlan.price_monthly != null && userPlan.price_monthly > 0
                        ? `$${Number(userPlan.price_monthly).toFixed(2)}/mo.`
                        : 'N/A'}
                  </p>
                  {userPlan.credit_balance != null && (
                    <p><strong>Current balance:</strong> {Number(userPlan.credit_balance).toLocaleString()} credits</p>
                  )}
                  <p><strong>Credits:</strong> Usage-based. Extraction, migration, cleanup, transformation, schema analysis, and architect chat use credits. 1 credit = $0.01.</p>
                </>
              ) : (
                <>
                  {userPlan.credits_per_month != null && (
                    <p><strong>Credits:</strong>{' '}
                      {userPlan.credits_per_month === -1
                        ? (userPlan.plan_type?.toLowerCase() === 'enterprise' ? 'Custom' : 'Unlimited')
                        : `${Number(userPlan.credits_per_month).toLocaleString()} per month`}
                    </p>
                  )}
                  {userPlan.credit_balance != null && (
                    <p><strong>Current balance:</strong> {Number(userPlan.credit_balance).toLocaleString()} credits</p>
                  )}
                  {(userPlan.price_monthly != null && userPlan.price_monthly > 0) && (
                    <p><strong>Monthly amount:</strong> ${Number(userPlan.price_monthly).toFixed(2)}/mo.</p>
                  )}
                </>
              )}
            </div>
            {(userPlan.plan_type === 'free' || userPlan.plan_type === 'pay_as_you_go') && (
              <button className="upgrade-btn" onClick={() => navigate('/plans')}>
                {userPlan.plan_type === 'pay_as_you_go' ? 'Manage plan & buy credits' : 'Upgrade Plan'}
              </button>
            )}
            {(userPlan.stripe_customer_id || userPlan.stripe_subscription_id) && (
              <button
                type="button"
                className="upgrade-btn manage-subscription-btn"
                onClick={handleManageSubscription}
                disabled={portalLoading}
              >
                {portalLoading ? 'Opening…' : 'Manage subscription'}
              </button>
            )}
          </div>
        ) : (
          <div className="current-plan-card">
            <p>Unable to load plan information</p>
          </div>
        )}

        {(userPlan?.stripe_customer_id || userPlan?.stripe_subscription_id) && (
          <div className="billing-invoices-section">
            <div className="billing-invoices-header">
              <h4 className="billing-invoices-title">Invoices</h4>
              <select
                className="billing-invoices-month-select"
                value={invoiceMonth}
                onChange={(e) => setInvoiceMonth(e.target.value)}
                aria-label="Select month"
              >
                <option value="">All</option>
                {(() => {
                  const options = [];
                  const d = new Date();
                  for (let i = 0; i < 24; i++) {
                    const y = d.getFullYear();
                    const m = d.getMonth() + 1;
                    const value = `${y}-${String(m).padStart(2, '0')}`;
                    const label = d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
                    options.push(<option key={value} value={value}>{label}</option>);
                    d.setMonth(d.getMonth() - 1);
                  }
                  return options;
                })()}
              </select>
            </div>
            {invoicesLoading ? (
              <p className="billing-invoices-loading">Loading invoices…</p>
            ) : (
              <div className="billing-invoices-table-wrap">
                <table
                  className="billing-invoices-table"
                  style={{ tableLayout: 'fixed', width: '100%' }}
                >
                  <thead>
                    <tr>
                      <th style={{ width: 130, textAlign: 'left' }}>Date</th>
                      <th style={{ minWidth: 0 }}>Description</th>
                      <th style={{ width: 90, textAlign: 'left' }}>Status</th>
                      <th style={{ width: 100, textAlign: 'right' }}>Amount</th>
                      <th style={{ width: 80, textAlign: 'right' }}>Invoice</th>
                    </tr>
                  </thead>
                  <tbody>
                    {invoices.length === 0 ? (
                      <tr>
                        <td colSpan={5} className="billing-invoices-empty">No invoices for this period.</td>
                      </tr>
                    ) : (
                      invoices.map((inv) => (
                        <tr key={inv.id}>
                          <td
                            className="billing-invoice-date-cell"
                            style={{
                              width: 130,
                              minWidth: 130,
                              maxWidth: 130,
                              whiteSpace: 'nowrap',
                              boxSizing: 'border-box',
                              textAlign: 'left',
                            }}
                          >
                            {inv.date ? new Date(inv.date).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—'}
                          </td>
                          <td style={{ minWidth: 0, wordBreak: 'break-word' }}>
                            {inv.description || '—'}
                          </td>
                          <td style={{ textAlign: 'left' }}>
                            <span className={`billing-invoice-status billing-invoice-status-${(inv.status || '').toLowerCase()}`}>{inv.status || '—'}</span>
                          </td>
                          <td style={{ textAlign: 'right' }}>{inv.amount || '—'}</td>
                          <td style={{ textAlign: 'right' }}>
                            {inv.hosted_invoice_url ? (
                              <a href={inv.hosted_invoice_url} target="_blank" rel="noopener noreferrer" className="billing-invoice-view-link">
                                View <FiExternalLink size={12} />
                              </a>
                            ) : (
                              '—'
                            )}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );

  const renderDangerZone = () => (
    <div className="settings-section danger-zone">
      <div className="section-header">
        <h3><FiTrash2 /> Danger Zone</h3>
      </div>
      
      <div className="danger-actions">
        <div className="danger-item">
          <div className="danger-info">
            <h4>Delete Account</h4>
            <p>Permanently delete your account and all associated data. This action cannot be undone after 30 days.</p>
          </div>
          <button 
            className="danger-btn" 
            onClick={() => setShowDeleteAccountModal(true)}
          >
            Delete Account
          </button>
        </div>
      </div>
    </div>
  );

  const tabs = [
    { id: 'profile', label: 'Profile', icon: FiUser },
    { id: 'security', label: 'Security', icon: FiShield },
    { id: 'data-privacy', label: 'Data & Privacy', icon: FiDatabase },
    { id: 'notifications', label: 'Notifications', icon: FiBell },
    ...(billing
      ? [
          { id: 'usage', label: 'Usage', icon: FiActivity },
          { id: 'billing', label: 'Billing', icon: FiCreditCard },
        ]
      : []),
  ];

  return (
    <div className="settings-page">
      <div className="settings-header">
        <h1><FiUser /> Account Settings</h1>
        <p>Manage your account preferences and security settings</p>
      </div>

      {/* Global success/error (visible on all tabs, e.g. after export from Security) */}
      {success && (
        <div className="success-message settings-global-message">
          <FiCheck />
          <span>{success}</span>
        </div>
      )}
      {error && (
        <div className="error-message settings-global-message">
          <FiAlertTriangle />
          <span>{error}</span>
        </div>
      )}
      
      <div className="settings-container">
        <div className="settings-sidebar">
          {tabs.map(tab => (
            <button
              key={tab.id}
              className={`tab-button ${activeTab === tab.id ? 'active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <tab.icon />
              {tab.label}
            </button>
          ))}
        </div>
        
        <div className="settings-content">
          {activeTab === 'profile' && renderProfileTab()}
          {activeTab === 'security' && renderSecurityTab()}
          {activeTab === 'data-privacy' && renderDataPrivacyTab()}
          {activeTab === 'notifications' && renderNotificationsTab()}
          {activeTab === 'usage' && renderUsageTab()}
          {activeTab === 'billing' && renderBillingTab()}
          
          {activeTab === 'profile' && renderDangerZone()}
        </div>
      </div>
      
      {/* Change Password Modal */}
      <ChangePasswordModal
        isOpen={showChangePasswordModal}
        onClose={() => setShowChangePasswordModal(false)}
        onSuccess={handlePasswordChangeSuccess}
      />
      
      {/* Login History Modal */}
      <LoginHistoryModal
        isOpen={showLoginHistoryModal}
        onClose={() => setShowLoginHistoryModal(false)}
      />
      
      {/* Delete Account Modal */}
      <DeleteAccountModal
        isOpen={showDeleteAccountModal}
        onClose={() => setShowDeleteAccountModal(false)}
        onConfirm={handleDeleteAccount}
        isLoading={isDeletingAccount}
      />
      
      {/* Export Data Modal */}
      {showDataRetentionModal && (
        <div className="modal-overlay">
          <div className="modal-content">
            <div className="modal-header">
              <h3>
                <FiDatabase /> Data retention
              </h3>
              <button
                type="button"
                className="modal-close"
                onClick={handleCloseDataRetentionModal}
                aria-label="Close"
              >
                <FiX />
              </button>
            </div>
            <div className="modal-body">
              <p>
                After this period we delete the detailed <strong>extracted data</strong> and{' '}
                <strong>migrated results</strong> we store for your account (including related file
                uploads). High-level summaries or metadata may be kept longer.
              </p>
              {dataRetentionSettings && (
                <div className="form-group">
                  <label htmlFor="dataRetentionDays">
                    Retention period (days){' '}
                    <span className="form-hint">
                      min 1 · max {dataRetentionSettings.max_allowed_days} for your plan
                    </span>
                  </label>
                  <input
                    id="dataRetentionDays"
                    type="number"
                    min={1}
                    max={dataRetentionSettings.max_allowed_days}
                    step={1}
                    className="form-input"
                    value={dataRetentionDraft}
                    onChange={(e) => setDataRetentionDraft(e.target.value)}
                  />
                </div>
              )}
              {dataRetentionModalError && (
                <div className="error-message">
                  <FiAlertTriangle /> {dataRetentionModalError}
                </div>
              )}
            </div>
            <div className="modal-footer">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={handleCloseDataRetentionModal}
                disabled={isSavingRetention}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleSaveDataRetention}
                disabled={isSavingRetention || !dataRetentionSettings}
              >
                {isSavingRetention ? (
                  <>
                    <div className="spinner" />
                    Saving…
                  </>
                ) : (
                  <>
                    <FiSave /> Save
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {showExportModal && (
        <div className="modal-overlay">
          <div className="modal-content">
            <div className="modal-header">
              <h3><FiDownload /> Export Migration Data</h3>
              <button className="modal-close" onClick={handleExportCancel}>
                <FiX />
              </button>
            </div>
            
            <div className="modal-body">
              <p>Select the date range for your migration data export:</p>
              
              <div className="form-group">
                <label htmlFor="exportStartDate">Start Date *</label>
                <input
                  type="date"
                  id="exportStartDate"
                  value={exportStartDate}
                  onChange={(e) => setExportStartDate(e.target.value)}
                  className="form-input"
                  required
                />
              </div>
              
              <div className="form-group">
                <label htmlFor="exportEndDate">End Date *</label>
                <input
                  type="date"
                  id="exportEndDate"
                  value={exportEndDate}
                  onChange={(e) => setExportEndDate(e.target.value)}
                  className="form-input"
                  required
                />
              </div>
              
              {error && (
                <div className="error-message">
                  <FiAlertTriangle /> {error}
                </div>
              )}
            </div>
            
            <div className="modal-footer">
              <button 
                className="btn btn-secondary" 
                onClick={handleExportCancel}
              >
                Cancel
              </button>
              {isExporting && (
                <button 
                  className="btn btn-warning" 
                  onClick={handleExportReset}
                >
                  Reset
                </button>
              )}
              <button 
                className="btn btn-primary" 
                onClick={handleExportSubmit}
                disabled={isExporting || !exportStartDate || !exportEndDate}
              >
                {isExporting ? (
                  <>
                    <div className="spinner"></div>
                    Exporting...
                  </>
                ) : (
                  <>
                    <FiDownload />
                    Export Data
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default SettingsPage;
