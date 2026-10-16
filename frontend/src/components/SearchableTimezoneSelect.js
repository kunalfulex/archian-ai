import React, { useState, useRef, useEffect } from 'react';
import { FiSearch, FiChevronDown, FiChevronUp } from 'react-icons/fi';
import './SearchableTimezoneSelect.css';

const SearchableTimezoneSelect = ({ value, onChange, disabled, timezones }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [filteredGroups, setFilteredGroups] = useState({});
  const dropdownRef = useRef(null);
  const searchInputRef = useRef(null);

  // Group timezones by country if not already grouped
  const groupedTimezones = React.useMemo(() => {
    if (!timezones || timezones.length === 0) return {};
    
    // Check if already grouped (object with countries as keys)
    if (typeof timezones === 'object' && !Array.isArray(timezones)) {
      return timezones;
    }
    
    // If flat list, group by country
    const grouped = {};
    timezones.forEach(tz => {
      if (typeof tz === 'string' && tz.includes('|')) {
        const [, display] = tz.split('|');
        const country = display.includes(',') ? display.split(',')[1].trim() : display;
        const cleanCountry = country.split('(')[0].trim();
        
        if (!grouped[cleanCountry]) {
          grouped[cleanCountry] = [];
        }
        grouped[cleanCountry].push({ value: tz, label: display });
      }
    });
    
    // Sort countries and timezones within each country
    const sorted = {};
    Object.keys(grouped).sort().forEach(country => {
      sorted[country] = grouped[country].sort((a, b) => a.label.localeCompare(b.label));
    });
    
    return sorted;
  }, [timezones]);

  // Initialize filtered groups
  useEffect(() => {
    setFilteredGroups(groupedTimezones);
  }, [groupedTimezones]);

  // Filter groups based on search query
  useEffect(() => {
    if (!searchQuery.trim()) {
      setFilteredGroups(groupedTimezones);
      return;
    }

    const query = searchQuery.toLowerCase();
    const filtered = {};

    Object.keys(groupedTimezones).forEach(country => {
      const matchingTimezones = groupedTimezones[country].filter(tz =>
        tz.label.toLowerCase().includes(query) || country.toLowerCase().includes(query)
      );

      if (matchingTimezones.length > 0) {
        filtered[country] = matchingTimezones;
      }
    });

    setFilteredGroups(filtered);
  }, [searchQuery, groupedTimezones]);

  // Get display name for selected value
  const getDisplayName = () => {
    if (!value) return 'Select timezone';
    
    // Find the display name from grouped timezones
    for (const country in groupedTimezones) {
      const found = groupedTimezones[country].find(tz => tz.value === value);
      if (found) return found.label;
    }
    
    // Fallback: parse from value if it's in IANA|Display format
    if (typeof value === 'string' && value.includes('|')) {
      return value.split('|')[1];
    }
    
    return value;
  };

  // Handle click outside to close dropdown
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false);
        setSearchQuery('');
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [isOpen]);

  // Focus search input when dropdown opens
  useEffect(() => {
    if (isOpen && searchInputRef.current) {
      searchInputRef.current.focus();
    }
  }, [isOpen]);

  const handleSelect = (tzValue) => {
    // Call onChange with proper event structure
    if (onChange) {
      const event = {
        target: {
          name: 'timezone',
          value: tzValue
        }
      };
      onChange(event);
    }
    setIsOpen(false);
    setSearchQuery('');
  };

  const handleToggle = () => {
    if (!disabled) {
      setIsOpen(!isOpen);
      if (!isOpen) {
        setSearchQuery('');
      }
    }
  };

  return (
    <div className="searchable-timezone-select" ref={dropdownRef}>
      <div
        className={`timezone-select-trigger ${isOpen ? 'open' : ''} ${disabled ? 'disabled' : ''}`}
        onClick={handleToggle}
      >
        <span className="timezone-select-value">{getDisplayName()}</span>
        {isOpen ? <FiChevronUp /> : <FiChevronDown />}
      </div>

      {isOpen && (
        <div className="timezone-select-dropdown">
          <div className="timezone-search-box">
            <FiSearch className="search-icon" />
            <input
              ref={searchInputRef}
              type="text"
              placeholder="Search by country or city..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="timezone-search-input"
            />
          </div>

          <div className="timezone-select-options">
            {Object.keys(filteredGroups).length === 0 ? (
              <div className="timezone-no-results">No timezones found</div>
            ) : (
              Object.keys(filteredGroups).map(country => (
                <div key={country} className="timezone-country-group">
                  <div className="timezone-country-header">{country}</div>
                  {filteredGroups[country].map((tz, index) => (
                    <div
                      key={`${country}-${index}`}
                      className={`timezone-option ${value === tz.value ? 'selected' : ''}`}
                      onClick={() => handleSelect(tz.value)}
                    >
                      {tz.label}
                    </div>
                  ))}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default SearchableTimezoneSelect;

