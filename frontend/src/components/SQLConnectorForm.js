import React, { useState, useEffect } from 'react';
import { FiDatabase, FiEye, FiEyeOff, FiCheck, FiX, FiTrash2, FiList } from 'react-icons/fi';
import api from '../api';
import SqlConnectorTablesModal from './SqlConnectorTablesModal';
import ConnectorMetadataSyncInline from './ConnectorMetadataSyncInline';
import './SQLConnectorForm.css';

const EMBEDDED_TOP_ROW_FIELDS = ['name', 'host'];
const EMBEDDED_GRID_FIELDS = ['port', 'database_name', 'username', 'password'];

const SQLConnectorForm = ({
  connectorType,
  editingConnector,
  onSave,
  onDelete,
  isLoading,
  embedded = false,
  connectionDescription = '',
  onConnectionDescriptionChange,
}) => {
  const [tablesModalOpen, setTablesModalOpen] = useState(false);
  const [formData, setFormData] = useState({
    name: '',
    host: '',
    port: '',
    database_name: '',
    username: '',
    password: '',
    ssl_enabled: false,
    connection_string: '',
    additional_config: {}
  });
  
  const [showPassword, setShowPassword] = useState(false);
  const [errors, setErrors] = useState({});
  const [testResult, setTestResult] = useState(null);
  const [isTestingConnection, setIsTestingConnection] = useState(false);

  // Populate form when editing
  useEffect(() => {
    if (editingConnector) {
      setFormData({
        name: editingConnector.name || '',
        host: editingConnector.host || '',
        port: editingConnector.port || '',
        database_name: editingConnector.database_name || '',
        username: editingConnector.username || '',
        password: '', // Don't populate password for security
        ssl_enabled: editingConnector.ssl_enabled || false,
        connection_string: editingConnector.connection_string || '',
        additional_config: editingConnector.additional_config || {}
      });
    }
  }, [editingConnector]);

  const connectorConfigs = {
    mysql: {
      name: 'MySQL',
      defaultPort: 3306,
      icon: '/assets/connectors/MySQL-Logo.wine.png',
      fields: ['name', 'host', 'port', 'database_name', 'username', 'password', 'ssl_enabled']
    },
    postgresql: {
      name: 'PostgreSQL',
      defaultPort: 5432,
      icon: '/assets/connectors/postgresql-logo.png',
      fields: ['name', 'host', 'port', 'database_name', 'username', 'password', 'ssl_enabled']
    },
    oracle: {
      name: 'Oracle',
      defaultPort: 1521,
      icon: '/assets/connectors/Oracle.png',
      fields: ['name', 'host', 'port', 'database_name', 'username', 'password', 'ssl_enabled']
    },
    bigquery: {
      name: 'Google BigQuery',
      defaultPort: null,
      icon: '/assets/connectors/BigQuery.png',
      fields: ['name', 'database_name', 'additional_config'],
      specialFields: ['service_account_key']
    },
    mssql: {
      name: 'Microsoft SQL Server',
      defaultPort: 1433,
      icon: '/assets/connectors/mssql-logo.png',
      fields: ['name', 'host', 'port', 'database_name', 'username', 'password', 'ssl_enabled']
    },
    snowflake: {
      name: 'Snowflake',
      defaultPort: null,
      icon: '/assets/connectors/snowflake-logo.png',
      fields: ['name', 'host'],
      fieldRows: [
        ['database_name', 'snowflake_warehouse'],
        ['username', 'password'],
      ],
      specialFields: ['snowflake_role', 'snowflake_schema', 'snowflake_private_key'],
    }
  };

  const config = connectorConfigs[connectorType] || connectorConfigs.mysql;

  const formIncludesField = (field) => {
    if (config.fields?.includes(field)) return true;
    if (config.fieldRows?.some((row) => row.includes(field))) return true;
    if (config.specialFields?.includes(field)) return true;
    return false;
  };

  const handleInputChange = (field, value) => {
    setFormData(prev => ({
      ...prev,
      [field]: value
    }));
    
    // Clear error when user starts typing
    if (errors[field]) {
      setErrors(prev => ({
        ...prev,
        [field]: ''
      }));
    }
  };

  const validateForm = () => {
    const newErrors = {};
    
    if (!formData.name.trim()) {
      newErrors.name = 'Connection name is required';
    }
    
    if (config.fields.includes('host') && !formData.host.trim()) {
      newErrors.host = 'Host is required';
    }
    
    if (formIncludesField('database_name') && !formData.database_name.trim()) {
      newErrors.database_name = 'Database name is required';
    }

    if (formIncludesField('username') && !formData.username.trim()) {
      newErrors.username = 'Username is required';
    }
    
    // Password is optional for all database types
    
    if (connectorType === 'bigquery' && !formData.additional_config.service_account_key) {
      newErrors.service_account_key = 'Service Account Key is required for BigQuery';
    }

    if (connectorType === 'snowflake') {
      const ac = formData.additional_config || {};
      if (!ac.role?.trim()) {
        newErrors.snowflake_role = 'Role is required';
      }
      if (!ac.warehouse?.trim()) {
        newErrors.snowflake_warehouse = 'Warehouse is required';
      }
      if (!ac.schema?.trim()) {
        newErrors.snowflake_schema = 'Schema is required';
      }
      const hasPassword = Boolean(formData.password?.trim());
      const hasKey = Boolean(ac.private_key_pem?.trim());
      if (!hasPassword && !hasKey && !editingConnector) {
        newErrors.password = 'Password or private key is required';
      }
    }
    
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleTestConnection = async () => {
    if (!validateForm()) return;
    
    setTestResult(null);
    setIsTestingConnection(true);
    
    const requestData = {
      connector_type: connectorType,
      ...formData
    };
    
    try {
      const response = await api.post('/api/sql-connectors/test-connection', requestData);
      setTestResult(response.data);
    } catch (error) {
      console.error('Test connection error:', error);
      setTestResult({
        status: 'error',
        message: error.response?.data?.message || 'Connection test failed'
      });
    } finally {
      setIsTestingConnection(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    
    if (!validateForm()) return;
    
    const submitData = {
      connector_type: connectorType,
      ...formData,
    };

    // Set default port if not provided
    if (config.defaultPort && !formData.port) {
      submitData.port = config.defaultPort;
    }

    // Snowflake/BigQuery omit port — empty string breaks API validation on save
    if (!config.defaultPort && (submitData.port === '' || submitData.port == null)) {
      delete submitData.port;
    }

    onSave(submitData);
  };

  const renderField = (field) => {
    switch (field) {
      case 'name':
        return (
          <div className="form-group">
            <label className="form-label">
              Connection Name *
            </label>
            <input
              type="text"
              placeholder="My Database Connection"
              value={formData.name}
              onChange={(e) => handleInputChange('name', e.target.value)}
              className={errors.name ? 'error' : ''}
            />
            {errors.name && <span className="error-message">{errors.name}</span>}
          </div>
        );
        
      case 'host':
        return (
          <div className="form-group">
            <label className="form-label">
              {connectorType === 'snowflake' ? 'Account identifier *' : 'Host *'}
            </label>
            <input
              type="text"
              placeholder={
                connectorType === 'snowflake'
                  ? 'xy12345.us-east-1.aws'
                  : 'localhost or your-database-host.com'
              }
              value={formData.host}
              onChange={(e) => handleInputChange('host', e.target.value)}
              className={errors.host ? 'error' : ''}
            />
            {errors.host && <span className="error-message">{errors.host}</span>}
          </div>
        );
        
      case 'port':
        return (
          <div className="form-group">
            <label className="form-label">
              Port
            </label>
            <input
              type="number"
              placeholder={config.defaultPort || 'Port number'}
              value={formData.port}
              onChange={(e) => handleInputChange('port', parseInt(e.target.value) || '')}
              className={errors.port ? 'error' : ''}
            />
            {config.defaultPort && (
              <span className="field-hint">Default: {config.defaultPort}</span>
            )}
          </div>
        );
        
      case 'database_name':
        return (
          <div className="form-group">
            <label className="form-label">
              {connectorType === 'bigquery'
                ? 'Dataset ID *'
                : connectorType === 'snowflake'
                  ? 'Database *'
                  : 'Database Name *'}
            </label>
            <input
              type="text"
              placeholder={
                connectorType === 'bigquery'
                  ? 'your_dataset_id'
                  : connectorType === 'snowflake'
                    ? 'ANALYTICS'
                    : 'your_database_name'
              }
              value={formData.database_name}
              onChange={(e) => handleInputChange('database_name', e.target.value)}
              className={errors.database_name ? 'error' : ''}
            />
            {errors.database_name && <span className="error-message">{errors.database_name}</span>}
          </div>
        );
        
      case 'username':
        return (
          <div className="form-group">
            <label className="form-label">
              Username *
            </label>
            <input
              type="text"
              placeholder="your_username"
              value={formData.username}
              onChange={(e) => handleInputChange('username', e.target.value)}
              className={errors.username ? 'error' : ''}
            />
            {errors.username && <span className="error-message">{errors.username}</span>}
          </div>
        );
        
      case 'password':
        return (
          <div className="form-group">
            <label className="form-label">
              Password
            </label>
            <div className="password-input-container">
              <input
                type={showPassword ? 'text' : 'password'}
                placeholder={
                  connectorType === 'snowflake'
                    ? 'Optional if using private key below'
                    : 'Leave empty if no password'
                }
                value={formData.password}
                onChange={(e) => handleInputChange('password', e.target.value)}
                className={errors.password ? 'error' : ''}
              />
              <button
                type="button"
                className="password-toggle"
                onClick={() => setShowPassword(!showPassword)}
              >
                {showPassword ? <FiEyeOff /> : <FiEye />}
              </button>
            </div>
            {errors.password && <span className="error-message">{errors.password}</span>}
          </div>
        );

      case 'snowflake_role':
        return (
          <div className="form-group">
            <label className="form-label">Role *</label>
            <input
              type="text"
              placeholder="ARCHIAN_RO"
              value={formData.additional_config.role || ''}
              onChange={(e) => handleInputChange('additional_config', {
                ...formData.additional_config,
                role: e.target.value,
              })}
              className={errors.snowflake_role ? 'error' : ''}
            />
            {errors.snowflake_role && <span className="error-message">{errors.snowflake_role}</span>}
          </div>
        );

      case 'snowflake_warehouse':
        return (
          <div className="form-group">
            <label className="form-label">Warehouse *</label>
            <input
              type="text"
              placeholder="COMPUTE_WH"
              value={formData.additional_config.warehouse || ''}
              onChange={(e) => handleInputChange('additional_config', {
                ...formData.additional_config,
                warehouse: e.target.value,
              })}
              className={errors.snowflake_warehouse ? 'error' : ''}
            />
            {errors.snowflake_warehouse && (
              <span className="error-message">{errors.snowflake_warehouse}</span>
            )}
          </div>
        );

      case 'snowflake_schema':
        return (
          <div className="form-group">
            <label className="form-label">Schema *</label>
            <input
              type="text"
              placeholder="PUBLIC"
              value={formData.additional_config.schema || ''}
              onChange={(e) => handleInputChange('additional_config', {
                ...formData.additional_config,
                schema: e.target.value,
              })}
              className={errors.snowflake_schema ? 'error' : ''}
            />
            {errors.snowflake_schema && <span className="error-message">{errors.snowflake_schema}</span>}
          </div>
        );

      case 'snowflake_private_key':
        return (
          <div className="form-group">
            <label className="form-label">Private key (PEM)</label>
            <textarea
              placeholder="Optional — paste service account private key if not using password"
              value={formData.additional_config.private_key_pem || ''}
              onChange={(e) => handleInputChange('additional_config', {
                ...formData.additional_config,
                private_key_pem: e.target.value,
              })}
              rows={5}
            />
            <span className="field-hint">Use key-pair auth for service users without a password.</span>
          </div>
        );
        
      case 'ssl_enabled':
        return (
          <div className="form-group checkbox-group">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={formData.ssl_enabled}
                onChange={(e) => handleInputChange('ssl_enabled', e.target.checked)}
              />
              <span className="checkbox-text">Enable SSL Connection</span>
            </label>
          </div>
        );
        
      case 'service_account_key':
        return (
          <div className="form-group">
            <label className="form-label">
              Service Account Key (JSON) *
            </label>
            <textarea
              placeholder="Paste your Google Cloud Service Account JSON key here..."
              value={formData.additional_config.service_account_key || ''}
              onChange={(e) => handleInputChange('additional_config', {
                ...formData.additional_config,
                service_account_key: e.target.value
              })}
              className={errors.service_account_key ? 'error' : ''}
              rows={6}
            />
            {errors.service_account_key && <span className="error-message">{errors.service_account_key}</span>}
            <span className="field-hint">
              Get this from Google Cloud Console → IAM & Admin → Service Accounts
            </span>
          </div>
        );
        
      default:
        return null;
    }
  };

  const renderFieldRows = () =>
    config.fieldRows.map((row, rowIndex) => (
      <div key={`field-row-${rowIndex}`} className="sql-form-grid-2x2">
        {row.map((field) => (
          <div key={field} className="sql-form-grid-cell">
            {renderField(field)}
          </div>
        ))}
      </div>
    ));

  const renderFormFields = () => {
    if (config.fieldRows?.length) {
      return (
        <>
          {config.fields.map((field) => (
            <div key={field}>{renderField(field)}</div>
          ))}
          {renderFieldRows()}
          {config.specialFields?.map((field) => (
            <div key={field}>{renderField(field)}</div>
          ))}
        </>
      );
    }

    if (!embedded) {
      return (
        <>
          {config.fields.map((field) => (
            <div key={field}>{renderField(field)}</div>
          ))}
          {config.specialFields?.map((field) => (
            <div key={field}>{renderField(field)}</div>
          ))}
        </>
      );
    }

    const topRowFields = EMBEDDED_TOP_ROW_FIELDS.filter((f) => config.fields.includes(f));
    const gridFields = EMBEDDED_GRID_FIELDS.filter((f) => config.fields.includes(f));
    const reservedFields = new Set([...EMBEDDED_TOP_ROW_FIELDS, ...EMBEDDED_GRID_FIELDS]);
    const fieldsAfterGrid = config.fields.filter((f) => !reservedFields.has(f));

    return (
      <>
        {topRowFields.length > 0 && (
          <div className="sql-form-grid-2x2">
            {topRowFields.map((field) => (
              <div key={field} className="sql-form-grid-cell">
                {renderField(field)}
              </div>
            ))}
          </div>
        )}
        {gridFields.length > 0 && (
          <div className="sql-form-grid-2x2">
            {gridFields.map((field) => (
              <div key={field} className="sql-form-grid-cell">
                {renderField(field)}
              </div>
            ))}
          </div>
        )}
        {fieldsAfterGrid.map((field) => (
          <div key={field}>{renderField(field)}</div>
        ))}
        {config.specialFields?.map((field) => (
          <div key={field}>{renderField(field)}</div>
        ))}
      </>
    );
  };
  return (
    <div className={`sql-connector-form${embedded ? ' sql-connector-form--embedded' : ''}`}>
      <div className={`connector-header${embedded ? ' connector-header--embedded' : ''}`}>
        <div className="connector-logo">
          <img
            src={config.icon}
            alt={config.name}
            className={`connector-icon${connectorType === 'snowflake' ? ' connector-icon--snowflake' : ''}`}
          />
        </div>
        <div className="connector-info">
          {embedded ? (
            <div className="connection-status">
              <div className="status-indicator" />
              <h3 className="gs-connection-title sql-connection-title">
                {config.name} connection
              </h3>
            </div>
          ) : (
            <>
              <h3>{config.name} Connection</h3>
              <p>Configure your {config.name} database connection</p>
            </>
          )}
        </div>
      </div>

      <form onSubmit={handleSubmit} className="connector-form">
        {renderFormFields()}

        {embedded && editingConnector?.id ? (
          <>
            <ConnectorMetadataSyncInline
              connectorType="sql"
              connectorId={editingConnector.id}
              variant="status"
            />
            <div className="gs-connection-settings sql-connection-settings">
              <h4 className="gs-connection-settings-title">Connection settings</h4>
              <div className="sf-org-description-field">
                <label className="form-label" htmlFor="sql-connection-description">
                  Description{' '}
                  <span className="sf-org-description-hint">
                    (optional — helps Archian AI understand this connection)
                  </span>
                </label>
                <textarea
                  id="sql-connection-description"
                  className="form-input sf-org-description-textarea"
                  rows={3}
                  value={connectionDescription}
                  onChange={(e) => onConnectionDescriptionChange?.(e.target.value)}
                  placeholder="e.g. Production analytics warehouse for finance reporting"
                />
              </div>
              <ConnectorMetadataSyncInline
                connectorType="sql"
                connectorId={editingConnector.id}
                variant="compact-schedule"
              />
            </div>
          </>
        ) : null}

        {/* Test Result Display */}
        {testResult && (
          <div className={`test-result ${testResult.status}`}>
            {testResult.status === 'success' ? <FiCheck /> : <FiX />}
            {testResult.message}
          </div>
        )}

        {/* Submit Buttons */}
        <div className={`submit-actions${embedded ? ' submit-actions--embedded' : ''}`}>
          {embedded && editingConnector && (
            <button
              type="button"
              className="btn btn-danger gs-delete-connection-btn sql-delete-connection-btn"
              onClick={onDelete}
              disabled={isLoading}
            >
              <FiTrash2 />
              Delete connection
            </button>
          )}
          <div className="submit-actions-primary">
          {editingConnector?.id && !embedded && (
            <button
              type="button"
              className="sql-view-tables-btn"
              onClick={() => setTablesModalOpen(true)}
              disabled={isLoading}
            >
              <FiList aria-hidden />
              View tables
            </button>
          )}
          {editingConnector && !embedded && (
            <button
              type="button"
              className="delete-btn"
              onClick={onDelete}
              disabled={isLoading}
            >
              <FiTrash2 />
              Delete
            </button>
          )}
          <button
            type="button"
            className="test-connection-btn"
            onClick={handleTestConnection}
            disabled={isLoading || isTestingConnection}
          >
            <FiDatabase />
            {isTestingConnection ? 'Testing...' : 'Test'}
          </button>
          <button
            type="submit"
            className="save-btn"
            disabled={isLoading}
          >
            {isLoading ? 'Saving...' : 'Save'}
          </button>
          </div>
        </div>
      </form>

      {editingConnector?.id && !embedded && (
        <SqlConnectorTablesModal
          connectorId={editingConnector.id}
          isOpen={tablesModalOpen}
          onClose={() => setTablesModalOpen(false)}
        />
      )}
    </div>
  );
};

export default SQLConnectorForm;
