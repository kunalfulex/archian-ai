import React, { useState } from 'react';
import { FiX, FiUpload, FiPlay } from 'react-icons/fi';
import api from '../api';
import AlertModal from './AlertModal';
import '../FlowsPage.css';

const ResubmitFlowModal = ({ isOpen, flow, onClose }) => {
  const [selectedFile, setSelectedFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [migrationStep, setMigrationStep] = useState('');
  const [isStartingMigration, setIsStartingMigration] = useState(false);
  const [alertModal, setAlertModal] = useState({ isOpen: false, title: '', message: '', type: 'error' });

  const showAlert = (title, message, type = 'error') => {
    setAlertModal({ isOpen: true, title, message, type });
  };

  const closeAlert = () => {
    setAlertModal({ isOpen: false, title: '', message: '', type: 'error' });
  };

  const handleClose = () => {
    setSelectedFile(null);
    setDragOver(false);
    setMigrationStep('');
    onClose();
  };

  const handleFileChange = (e) => {
    setSelectedFile(e.target.files[0]);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length > 0) {
      setSelectedFile(e.dataTransfer.files[0]);
    }
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    setDragOver(true);
  };

  const handleDragLeave = () => {
    setDragOver(false);
  };

  const handleStartMigration = async () => {
    if (!selectedFile) {
      showAlert('File Required', 'Please select a file to continue', 'warning');
      return;
    }

    if (!flow.document_type || !flow.user_prompt) {
      showAlert(
        'Invalid Flow',
        'This flow is missing required information. Please ensure the flow has a document type and user prompt.',
        'warning'
      );
      return;
    }

    setIsStartingMigration(true);
    setMigrationStep('Uploading file and starting background processing...');

    try {
      const formData = new FormData();
      formData.append('file', selectedFile);
      formData.append('extraction_goals', flow.user_prompt || '');
      formData.append('flow_id', flow.id);

      const uploadResponse = await api.post('/upload-async/', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      if (!uploadResponse.data) {
        throw new Error('Upload failed');
      }

      if (uploadResponse.data.status !== 'processing') {
        throw new Error('Expected processing status but got: ' + uploadResponse.data.status);
      }

      const { job_id } = uploadResponse.data;
      setMigrationStep('Background processing started! Redirecting to job status...');
      handleClose();
      window.location.href = `/data-table/${job_id}`;
    } catch (error) {
      console.error('Error starting reusable flow migration:', error);

      if (error.response && error.response.status === 403) {
        const errorData = error.response.data;

        if (errorData.message && errorData.message.includes('exceeds your monthly limit')) {
          setMigrationStep('Plan limit exceeded - upgrade required');
          showAlert(
            'Plan Limit Exceeded',
            `${errorData.message}\n\nPlease upgrade your plan to continue migrating data.`,
            'warning'
          );
        } else {
          setMigrationStep(`Migration failed: ${errorData.message || 'Access denied'}`);
          showAlert('Migration Failed', errorData.message || 'Access denied');
        }
      } else if (error.response && error.response.status === 400) {
        const errorData = error.response.data;
        setMigrationStep(`Migration failed: ${errorData.detail || errorData.message || 'Bad request'}`);
        showAlert('Migration Failed', errorData.detail || errorData.message || 'Bad request');
      } else {
        setMigrationStep(`Error: ${error.message}`);
        showAlert('Migration Failed', `Failed to complete migration: ${error.message}`);
      }
    } finally {
      setIsStartingMigration(false);
    }
  };

  if (!isOpen || !flow) return null;

  return (
    <>
      <div className="flows-page modal-overlay resubmit-modal-overlay" onClick={handleClose}>
        <div className="modal-content resubmit-modal" onClick={(e) => e.stopPropagation()}>
          <div className="modal-header">
            <h3>🚀 Resubmit Flow: {flow.migration_name}</h3>
            <button className="modal-close" onClick={handleClose}>
              <FiX />
            </button>
          </div>
          <div className="modal-body">
            <div className="resubmit-info">
              <p className="resubmit-description">
                Upload a new file to use this saved flow configuration. The system will extract data using the same
                field mappings and schema.
              </p>

              <div className="file-upload-section">
                <h4>📁 Upload New File</h4>

                <div
                  className={`file-drop-zone ${dragOver ? 'drag-over' : ''} ${selectedFile ? 'file-selected' : ''}`}
                  onDrop={handleDrop}
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                >
                  {selectedFile ? (
                    <div className="file-selected-content">
                      <FiUpload className="upload-icon" />
                      <p className="file-name">{selectedFile.name}</p>
                      <p className="file-size">{(selectedFile.size / 1024 / 1024).toFixed(2)} MB</p>
                      <button type="button" className="btn btn-secondary btn-sm" onClick={() => setSelectedFile(null)}>
                        Change File
                      </button>
                    </div>
                  ) : (
                    <div className="file-drop-content">
                      <FiUpload className="upload-icon" />
                      <p>Drag and drop a file here, or click to browse</p>
                      <input
                        type="file"
                        onChange={handleFileChange}
                        accept=".pdf,.doc,.docx,.txt,.csv,.xlsx,.xls"
                        className="file-input"
                      />
                    </div>
                  )}
                </div>
              </div>

              {migrationStep && (
                <div className="">
                  <p className="migration-step">{migrationStep}</p>
                </div>
              )}

              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={handleStartMigration}
                  disabled={!selectedFile || isStartingMigration}
                >
                  {isStartingMigration ? (
                    <>
                      <div className="spinner-small"></div>
                      Processing File & Migrating...
                    </>
                  ) : (
                    <>
                      <FiPlay /> Start Migration
                    </>
                  )}
                </button>
                <button type="button" className="btn btn-secondary" onClick={handleClose} disabled={isStartingMigration}>
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <AlertModal
        isOpen={alertModal.isOpen}
        onClose={closeAlert}
        title={alertModal.title}
        message={alertModal.message}
        type={alertModal.type}
      />
    </>
  );
};

export default ResubmitFlowModal;
