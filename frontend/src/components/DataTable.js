import React, { useState, useMemo } from 'react';
import { useTable, usePagination, useSortBy, useGlobalFilter } from 'react-table';
import { FiChevronLeft, FiChevronRight, FiSearch, FiChevronUp, FiChevronDown } from 'react-icons/fi';
/*
 * IMPORTANT: DataTable CSS is NOT imported here to prevent global CSS conflicts.
 * 
 * The DataTable.css should be imported ONLY in the pages that use this component:
 * - SuccessfulMigrationsPage.js
 * - FailedMigrationsPage.js
 * 
 * This prevents the DataTable CSS from overriding styles in other pages like
 * DataTablePage, SalesforceMapper, etc.
 */
// import './DataTable.css';

const DataTable = ({ 
  columns, 
  data, 
  pageSizeOptions = [10, 20, 30, 50],
  defaultPageSize = 10,
  searchable = true,
  // Server-side pagination props
  serverSide = false,
  totalCount = 0,
  page = 1,
  pageSize: externalPageSize,
  onPageChange,
  onPageSizeChange,
  onSearchChange,
}) => {
  const [localPageSize, setLocalPageSize] = useState(externalPageSize ?? defaultPageSize);
  const [localSearchValue, setLocalSearchValue] = useState('');

  const currentPageSize = serverSide ? (externalPageSize ?? defaultPageSize) : localPageSize;

  // Global filter function for search (client-side only)
  const globalFilter = useMemo(
    () => (rows, columnIds, filterValue) => {
      return rows.filter(row => {
        return Object.values(row.values).some(value => {
          if (value === null || value === undefined) return false;
          return String(value).toLowerCase().includes(filterValue.toLowerCase());
        });
      });
    },
    []
  );

  const tableConfig = useMemo(() => {
    const base = {
      columns,
      data,
      autoResetPage: false,
    };
    if (serverSide) {
      return {
        ...base,
        manualPagination: true,
        pageCount: Math.max(1, Math.ceil(totalCount / currentPageSize)),
        initialState: { pageIndex: 0, pageSize: currentPageSize },
        globalFilter: (rows) => rows, // No client-side filter when server-side
      };
    }
    return {
      ...base,
      initialState: { pageIndex: 0, pageSize: defaultPageSize },
      globalFilter,
    };
  }, [columns, data, serverSide, totalCount, currentPageSize, defaultPageSize, globalFilter]);

  const {
    getTableProps,
    getTableBodyProps,
    headerGroups,
    prepareRow,
    page: clientPage,
    canPreviousPage,
    canNextPage,
    pageCount,
    gotoPage,
    nextPage,
    previousPage,
    setPageSize,
    state: { pageIndex, globalFilter: clientSearchValue },
    setGlobalFilter,
  } = useTable(
    tableConfig,
    useGlobalFilter,
    useSortBy,
    usePagination
  );

  const pageToShow = serverSide ? page : pageIndex + 1;
  const pageCountToShow = serverSide ? Math.ceil(totalCount / currentPageSize) || 1 : pageCount;
  const rowsToRender = clientPage;
  const searchValue = serverSide ? localSearchValue : (clientSearchValue || '');
  const startItem = serverSide ? (page - 1) * currentPageSize + 1 : pageIndex * currentPageSize + 1;
  const endItem = serverSide
    ? Math.min(page * currentPageSize, totalCount)
    : Math.min((pageIndex + 1) * currentPageSize, data.length);
  const totalToShow = serverSide ? totalCount : data.length;

  // Update page size when it changes (client-side)
  React.useEffect(() => {
    if (!serverSide) {
      setPageSize(localPageSize);
    }
  }, [localPageSize, setPageSize, serverSide]);

  const handleSearchChange = (e) => {
    const v = e.target.value;
    if (serverSide && onSearchChange) {
      setLocalSearchValue(v);
      // Only update display; search fires on Enter via onKeyDown
    } else {
      setGlobalFilter(v);
    }
  };

  const handleSearchKeyDown = (e) => {
    if (e.key === 'Enter' && serverSide && onSearchChange) {
      onSearchChange(localSearchValue);
    }
  };

  const handleServerPageChange = (newPage) => {
    if (onPageChange) onPageChange(newPage);
  };

  return (
    <div className="data-table-container">
      {/* Search and Controls */}
      <div className="data-table-controls">
        {searchable && (
          <div className="search-container">
            <FiSearch className="search-icon" />
            <input
              type="text"
              placeholder={serverSide ? "Search all columns... (press Enter to search)" : "Search all columns..."}
              value={searchValue || ''}
              onChange={handleSearchChange}
              onKeyDown={handleSearchKeyDown}
              className="search-input"
            />
          </div>
        )}
        
        <div className="page-size-container">
          <span>Show:</span>
          <select
            value={currentPageSize}
            onChange={e => {
              const val = Number(e.target.value);
              if (serverSide && onPageSizeChange) {
                onPageSizeChange(val);
              } else {
                setLocalPageSize(val);
              }
            }}
            className="page-size-select"
          >
            {pageSizeOptions.map(size => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
          <span>per page</span>
        </div>
      </div>

      {/* Table */}
      <div className="table-wrapper remove-padding">
        <table {...getTableProps()} className="data-table">
          <thead>
            {headerGroups.map(headerGroup => (
              <tr {...headerGroup.getHeaderGroupProps()}>
                {headerGroup.headers.map(column => (
                  <th
                    {...column.getHeaderProps(column.getSortByToggleProps())}
                    className={`table-header ${column.canSort ? 'sortable' : ''}`}
                  >
                    <div className="header-content">
                      {column.render('Header')}
                      {column.canSort && (
                        <span className="sort-indicator">
                          {column.isSorted ? (
                            column.isSortedDesc ? (
                              <FiChevronDown className="sort-icon" />
                            ) : (
                              <FiChevronUp className="sort-icon" />
                            )
                          ) : (
                            <FiChevronUp className="sort-icon inactive" />
                          )}
                        </span>
                      )}
                    </div>
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody {...getTableBodyProps()}>
            {rowsToRender.map(row => {
              prepareRow(row);
              return (
                <tr {...row.getRowProps()} className="table-row">
                  {row.cells.map(cell => (
                    <td 
                      {...cell.getCellProps()} 
                      className={`table-cell ${cell.column.className || ''}`}
                    >
                      {cell.render('Cell')}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination - show when multiple pages or when server-side (to display count) */}
      {((serverSide && totalToShow > 0) || (!serverSide && pageCount > 1)) && (
        <div className="pagination-container">
          <div className="pagination-info">
            <span>
              Page {pageToShow} of {pageCountToShow}
            </span>
            <span>
              Showing {totalToShow > 0 ? startItem : 0} to {endItem} of {totalToShow} results
            </span>
          </div>
          
          <div className="pagination-controls">
            <button
              onClick={() => serverSide ? handleServerPageChange(1) : gotoPage(0)}
              disabled={serverSide ? page <= 1 : !canPreviousPage}
              className="pagination-btn"
              title="First page"
            >
              <FiChevronLeft />
              <FiChevronLeft />
            </button>
            
            <button
              onClick={() => serverSide ? handleServerPageChange(page - 1) : previousPage()}
              disabled={serverSide ? page <= 1 : !canPreviousPage}
              className="pagination-btn"
              title="Previous page"
            >
              <FiChevronLeft />
            </button>
            
            <button
              onClick={() => serverSide ? handleServerPageChange(page + 1) : nextPage()}
              disabled={serverSide ? page >= pageCountToShow : !canNextPage}
              className="pagination-btn"
              title="Next page"
            >
              <FiChevronRight />
            </button>
            
            <button
              onClick={() => serverSide ? handleServerPageChange(pageCountToShow) : gotoPage(pageCount - 1)}
              disabled={serverSide ? page >= pageCountToShow : !canNextPage}
              className="pagination-btn"
              title="Last page"
            >
              <FiChevronRight />
              <FiChevronRight />
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default DataTable;
