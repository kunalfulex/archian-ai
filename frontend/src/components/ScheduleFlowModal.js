import React, { useState, useEffect, useCallback, useRef } from 'react';
import { FiClock, FiX, FiCalendar } from 'react-icons/fi';
import { useUserTimezone } from '../hooks/useUserTimezone';
import './ScheduleFlowModal.css';

const ScheduleFlowModal = ({ 
  isOpen, 
  onClose, 
  onSave, 
  flowId, 
  existingSchedule = null,
  isLoading = false 
}) => {
  const { userTimezone } = useUserTimezone();
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [frequency, setFrequency] = useState('daily');
  const [timeOfDay, setTimeOfDay] = useState('09:00');
  const [dayOfMonth, setDayOfMonth] = useState(1);
  const [month, setMonth] = useState(1);
  const [yearlyDate, setYearlyDate] = useState(''); // Date string in YYYY-MM-DD format for yearly schedules
  const [timezone, setTimezone] = useState('');
  const [nextRunPreview, setNextRunPreview] = useState('');
  const timeInputRef = useRef(null);
  const dateInputRef = useRef(null);

  useEffect(() => {
    if (existingSchedule) {
      setScheduleEnabled(existingSchedule.schedule_enabled || false);
      setFrequency(existingSchedule.schedule_frequency || 'daily');
      
      const config = existingSchedule.schedule_config || {};
      if (config.time_of_day) {
        setTimeOfDay(config.time_of_day);
      }
      if (config.day_of_month) {
        setDayOfMonth(config.day_of_month);
      }
      if (config.month) {
        setMonth(config.month);
        // Construct date string for yearly date picker (use current year)
        if (config.month && config.day_of_month) {
          const currentYear = new Date().getFullYear();
          const dateStr = `${currentYear}-${String(config.month).padStart(2, '0')}-${String(config.day_of_month).padStart(2, '0')}`;
          setYearlyDate(dateStr);
        }
      }
      
      setTimezone(existingSchedule.schedule_timezone || userTimezone);
    } else {
      setScheduleEnabled(false);
      setFrequency('daily');
      setTimeOfDay('09:00');
      setDayOfMonth(1);
      setMonth(1);
      const currentYear = new Date().getFullYear();
      setYearlyDate(`${currentYear}-01-01`);
      setTimezone(userTimezone);
    }
  }, [existingSchedule, userTimezone, isOpen]);

  const calculateNextRunPreview = useCallback(() => {
    // This is a simplified preview - actual calculation is done on backend
    if (!scheduleEnabled) {
      setNextRunPreview('');
      return;
    }
    
    let preview = '';
    
    if (frequency === 'hourly') {
      preview = `Every hour at minute 0`;
    } else if (frequency === 'daily') {
      preview = `Daily at ${timeOfDay}`;
    } else if (frequency === 'monthly') {
      preview = `Monthly on day ${dayOfMonth} at ${timeOfDay}`;
    } else if (frequency === 'yearly') {
      const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
      preview = `Yearly on ${monthNames[month - 1]} ${dayOfMonth} at ${timeOfDay}`;
    }
    
    setNextRunPreview(preview);
  }, [scheduleEnabled, frequency, timeOfDay, dayOfMonth, month]);

  useEffect(() => {
    calculateNextRunPreview();
  }, [calculateNextRunPreview]);

  // Initialize yearly date when switching to yearly frequency
  useEffect(() => {
    if (frequency === 'yearly' && !yearlyDate) {
      const currentYear = new Date().getFullYear();
      const dateStr = `${currentYear}-${String(month).padStart(2, '0')}-${String(dayOfMonth).padStart(2, '0')}`;
      setYearlyDate(dateStr);
    }
  }, [frequency, month, dayOfMonth, yearlyDate]);

  const handleSave = () => {
    const scheduleData = {
      schedule_enabled: scheduleEnabled,
      schedule_frequency: scheduleEnabled ? frequency : null,
      time_of_day: scheduleEnabled && frequency !== 'hourly' ? timeOfDay : null,
      day_of_month: scheduleEnabled && (frequency === 'monthly' || frequency === 'yearly') ? dayOfMonth : null,
      month: scheduleEnabled && frequency === 'yearly' ? month : null,
      // Always use current user timezone (timezone field is hidden, so always use userTimezone)
      timezone: scheduleEnabled ? userTimezone : null
    };
    
    onSave(scheduleData);
  };

  const handleDisable = () => {
    onSave({
      schedule_enabled: false,
      schedule_frequency: null,
      time_of_day: null,
      day_of_month: null,
      month: null,
      timezone: null
    });
  };

  if (!isOpen) return null;

  return (
    <div className="schedule-flow-modal-overlay" onClick={onClose}>
      <div className="schedule-flow-modal" onClick={(e) => e.stopPropagation()}>
        <div className="schedule-flow-modal-header">
          <div className="schedule-flow-modal-icon">
            <FiClock />
          </div>
          <h2>Schedule Flow</h2>
          <button className="schedule-flow-modal-close" onClick={onClose}>
            <FiX />
          </button>
        </div>

        <div className="schedule-flow-modal-content">
          <div className="schedule-enable-section">
            <label className="schedule-toggle">
              <input
                type="checkbox"
                checked={scheduleEnabled}
                onChange={(e) => setScheduleEnabled(e.target.checked)}
                disabled={isLoading}
              />
              <span>Enable scheduled execution</span>
            </label>
          </div>

          {scheduleEnabled && (
            <>
              <div className="schedule-field">
                <label>Frequency *</label>
                <select
                  value={frequency}
                  onChange={(e) => setFrequency(e.target.value)}
                  disabled={isLoading}
                >
                  <option value="hourly">Hourly</option>
                  <option value="daily">Daily</option>
                  <option value="monthly">Monthly</option>
                  <option value="yearly">Yearly</option>
                </select>
              </div>

              {frequency !== 'hourly' && (
                <div className="schedule-field">
                  <label>Time of Day *</label>
                  <input
                    ref={timeInputRef}
                    type="time"
                    value={timeOfDay}
                    onChange={(e) => setTimeOfDay(e.target.value)}
                    onClick={(e) => {
                      // Open time picker when clicking anywhere on the input field
                      if (timeInputRef.current && !isLoading) {
                        // Focus the input first
                        timeInputRef.current.focus();
                        // Try to show the picker if the browser supports it
                        if (timeInputRef.current.showPicker) {
                          try {
                            timeInputRef.current.showPicker();
                          } catch (error) {
                            // showPicker might fail in some browsers, that's okay
                          }
                        }
                      }
                    }}
                    disabled={isLoading}
                  />
                </div>
              )}

              {frequency === 'monthly' && (
                <div className="schedule-field">
                  <label>Day of Month *</label>
                  <select
                    value={dayOfMonth}
                    onChange={(e) => setDayOfMonth(parseInt(e.target.value))}
                    disabled={isLoading}
                  >
                    {Array.from({ length: 31 }, (_, i) => i + 1).map(day => (
                      <option key={day} value={day}>{day}</option>
                    ))}
                  </select>
                </div>
              )}

              {frequency === 'yearly' && (
                <div className="schedule-field">
                  <label>Date *</label>
                  <input
                    ref={dateInputRef}
                    type="date"
                    value={yearlyDate}
                    onChange={(e) => {
                      const selectedDate = e.target.value;
                      setYearlyDate(selectedDate);
                      if (selectedDate) {
                        // Parse date string directly to avoid timezone issues
                        // Date string format is YYYY-MM-DD
                        const [, monthStr, dayStr] = selectedDate.split('-');
                        const month = parseInt(monthStr, 10); // 1-12
                        const day = parseInt(dayStr, 10); // 1-31
                        setMonth(month);
                        setDayOfMonth(day);
                      }
                    }}
                    onClick={(e) => {
                      // Open date picker when clicking anywhere on the input field
                      if (dateInputRef.current && !isLoading) {
                        // Focus the input first
                        dateInputRef.current.focus();
                        // Try to show the picker if the browser supports it
                        if (dateInputRef.current.showPicker) {
                          try {
                            dateInputRef.current.showPicker();
                          } catch (error) {
                            // showPicker might fail in some browsers, that's okay
                          }
                        }
                      }
                    }}
                    disabled={isLoading}
                    min={`${new Date().getFullYear()}-01-01`}
                    max={`${new Date().getFullYear()}-12-31`}
                  />
                  <span className="field-hint">Select a date that will repeat yearly</span>
                </div>
              )}

              <div className="schedule-field schedule-field-timezone" style={{ display: 'none' }}>
                <label>Timezone</label>
                <input
                  type="text"
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value)}
                  placeholder={userTimezone}
                  disabled={isLoading}
                />
                <span className="field-hint">Defaults to your profile timezone</span>
              </div>

              {nextRunPreview && (
                <div className="next-run-preview">
                  <FiCalendar />
                  <span><strong>Next run:</strong> {nextRunPreview}</span>
                </div>
              )}
            </>
          )}

          <div className="schedule-flow-actions">
            {existingSchedule?.schedule_enabled && (
              <button
                className="schedule-flow-disable"
                onClick={handleDisable}
                disabled={isLoading}
              >
                Disable Schedule
              </button>
            )}
            <button
              className="schedule-flow-cancel"
              onClick={onClose}
              disabled={isLoading}
            >
              Cancel
            </button>
            <button
              className="schedule-flow-save"
              onClick={handleSave}
              disabled={isLoading || (scheduleEnabled && !frequency)}
            >
              {isLoading ? 'Saving...' : 'Save Schedule'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ScheduleFlowModal;

