import React from 'react';
import './App.css';
import { BrowserRouter as Router, Route, Routes } from 'react-router-dom';

import DataTablePage from './DataTablePage';
import SalesforceOAuthCallback from './SalesforceOAuthCallback';
import GoogleOAuthCallback from './GoogleOAuthCallback';
import LoginPage from './LoginPage';
import SignupPage from './SignupPage';
import VerifyPage from './VerifyPage';
import ForgotPasswordPage from './ForgotPasswordPage';
import ProtectedRoute from './ProtectedRoute';
import RedirectBasedOnAuth from './RedirectBasedOnAuth';
import ConnectionsPage from './ConnectionsPage';
import EnterpriseGraphsPage from './EnterpriseGraphsPage';
import SettingsPage from './SettingsPage';
import Layout from './Layout';  // ✅ ADD THIS
import ChatUploader from './ChatUploader';
import FlowsPage from './FlowsPage';
import FlowDetailPage from './FlowDetailPage';
import FlowDataTimelinePage from './FlowDataTimelinePage';
import FlowExtractionViewPage from './FlowExtractionViewPage';
import FlowMigrationResultsPage from './FlowMigrationResultsPage';
import MigrationProgressPage from './MigrationProgressPage';
import Dashboard from './Dashboard';
import SuccessfulMigrationsPage from './SuccessfulMigrationsPage';
import FailedMigrationsPage from './FailedMigrationsPage';
import PlanManagementPage from './PlanManagementPage';
import PaymentSuccessPage from './PaymentSuccessPage';
import SuperAdminPage from './SuperAdminPage';
import UpgradePage from './UpgradePage';
import { UserTimezoneProvider } from './hooks/useUserTimezone';
import { FeaturesProvider } from './hooks/useFeatures';
import RequireBilling from './RequireBilling';

function App() {
  return (
    <UserTimezoneProvider>
      <Router>
        <Routes>

          {/* Protected Routes with Layout */}
          <Route
            element={
              <ProtectedRoute>
                <FeaturesProvider>
                  <Layout />
                </FeaturesProvider>
              </ProtectedRoute>
            }
          >
            <Route path="/chat" element={<ChatUploader />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/flows/:flowId/timeline" element={<FlowDataTimelinePage />} />
            <Route path="/flows/:flowId/extraction/:extractionId" element={<FlowExtractionViewPage />} />
            <Route path="/flows/:flowId/migration/:batchId" element={<FlowMigrationResultsPage />} />
            <Route path="/flows/:flowId" element={<FlowDetailPage />} />
            <Route path="/flows" element={<FlowsPage />} />
            <Route path="/data-table" element={<DataTablePage />} />
            <Route path="/data-table/:jobId" element={<DataTablePage />} />
            <Route path="/connections" element={<ConnectionsPage />} />
            <Route path="/connections/enterprise-graphs" element={<EnterpriseGraphsPage />} />
            <Route path="/connections/enterprise-graphs/new" element={<EnterpriseGraphsPage />} />
            <Route path="/connections/enterprise-graphs/:graphId/edit" element={<EnterpriseGraphsPage />} />
            <Route path="/connections/enterprise-graphs/:graphId" element={<EnterpriseGraphsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/migration-progress/:jobId" element={<MigrationProgressPage />} />
            <Route path="/successful-migrations" element={<SuccessfulMigrationsPage />} />
            <Route path="/failed-migrations" element={<FailedMigrationsPage />} />
            <Route path="/plans" element={<RequireBilling><PlanManagementPage /></RequireBilling>} />
            <Route path="/payment-success" element={<RequireBilling><PaymentSuccessPage /></RequireBilling>} />
            <Route path="/super-admin" element={<SuperAdminPage />} />
            <Route path="/upgrade" element={<RequireBilling><UpgradePage /></RequireBilling>} />
          </Route>

          {/* Public Routes */}
          <Route path="/login" element={<LoginPage />} />
          <Route path="/signup" element={<SignupPage />} />
          <Route path="/verify" element={<VerifyPage />} />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/oauth/callback" element={<SalesforceOAuthCallback />} />
          <Route path="/auth/google/callback" element={<GoogleOAuthCallback />} />
          {/* Smart Redirect */}
          <Route path="/" element={<RedirectBasedOnAuth />} />
          <Route path="*" element={<RedirectBasedOnAuth />} />
        </Routes>
      </Router>
    </UserTimezoneProvider>
  );
}

export default App;
