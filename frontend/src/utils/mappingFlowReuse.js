import api from '../api';
import { getApiErrorMessage, getApiErrorCode } from './apiErrorDetail';
import { getSourceTypeKey } from './flowDisplayUtils';

function navigateFlowRerun(navigate, jobId, source, destinationLabel, data, showAlert, onReuseComplete) {
  const isSql = source === 'sql';
  const path = isSql ? '/data-table' : `/data-table/${jobId}`;
  navigate(path, {
    state: {
      job_id: jobId,
      source,
      message:
        data.message ||
        (isSql
          ? `SQL flow re-extraction started for ${destinationLabel}`
          : `Google Sheets flow re-extraction started for ${destinationLabel}`),
      isReusableFlow: true,
    },
  });
  setTimeout(() => {
    showAlert(
      'Success',
      data.message ||
        (isSql
          ? `SQL flow re-run started! We will push records to ${destinationLabel} and email you when the migration is complete.`
          : `Google Sheets flow re-run started! We will push records to ${destinationLabel} and email you when the migration is complete.`),
      'success',
    );
    onReuseComplete?.();
  }, 2000);
}

/**
 * Starts a reusable migration from a saved flow. Handles SQL / Google Sheets /
 * Salesforce re-runs (navigate + delayed success alert) or file upload (callback).
 */
export async function executeMappingFlowReuse(flow, navigate, { showAlert, onFileUploadRequired, onReuseComplete, onConnectionStale }) {
  try {
    const response = await api.post('/flow/reuse', {
      flow_id: flow.id,
      user_prompt: flow.user_prompt || '',
    });

    const data = response.data || {};
    const action = data.action || '';
    const destinationLabel = data.destination_name || data.destination_type || 'destination';
    const jobId = data.job_id;

    if (action === 'sql_rerun' && jobId) {
      navigateFlowRerun(navigate, jobId, 'sql', destinationLabel, data, showAlert, onReuseComplete);
      return;
    }

    if (action === 'gsheets_rerun' && jobId) {
      navigateFlowRerun(navigate, jobId, 'gsheets', destinationLabel, data, showAlert, onReuseComplete);
      return;
    }

    if (action === 'salesforce_rerun' && jobId) {
      navigate(`/data-table/${jobId}`, {
        state: {
          source: 'salesforce',
          message: data.message || `Salesforce flow extraction started for ${destinationLabel}`,
          isReusableFlow: true,
        },
      });
      setTimeout(() => {
        showAlert(
          'Success',
          data.message ||
            `Salesforce flow re-run started! We will push records to ${destinationLabel} and email you when the migration is complete.`,
          'success',
        );
        onReuseComplete?.();
      }, 2000);
      return;
    }

    if (action === 'file_upload' || data.status === 'pending_upload') {
      onReuseComplete?.();
      onFileUploadRequired(flow);
      return;
    }

    const src = getSourceTypeKey(flow);
    if (jobId && (src === 'sql' || src === 'gsheets')) {
      navigateFlowRerun(navigate, jobId, src, destinationLabel, data, showAlert, onReuseComplete);
      return;
    }
    if (jobId && src === 'salesforce') {
      navigate(`/data-table/${jobId}`, {
        state: {
          source: 'salesforce',
          message: data.message || `Salesforce flow extraction started for ${destinationLabel}`,
          isReusableFlow: true,
        },
      });
      setTimeout(() => {
        showAlert(
          'Success',
          data.message ||
            `Salesforce flow re-run started! We will push records to ${destinationLabel} and email you when the migration is complete.`,
          'success',
        );
        onReuseComplete?.();
      }, 2000);
      return;
    }

    throw new Error(data.message || 'Unsupported flow type');
  } catch (error) {
    console.error('Error reusing flow:', error);
    onReuseComplete?.();
    const code = getApiErrorCode(error);
    if (code === 'CONNECTION_STALE' || code === 'GOOGLE_SHEET_NOT_LINKED') {
      if (onConnectionStale) {
        onConnectionStale(flow, error);
        return;
      }
    }
    showAlert('Error', getApiErrorMessage(error));
  }
}
