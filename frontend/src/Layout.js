import React, { useState, useEffect } from 'react';
import Sidebar from './Sidebar';
import TopBar from './TopBar';
import { Outlet } from 'react-router-dom';

const Layout = () => {
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth <= 768);
    };

    checkMobile();
    window.addEventListener('resize', checkMobile);

    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  return (
    <div style={{ 
      display: 'flex', 
      height: '100vh',
      overflow: 'hidden',
      width: '100%'
    }}>
      {/* Show sidebar only on desktop */}
      {!isMobile && <Sidebar />}
      
      <div 
        style={{ 
          flex: 1, 
          overflowY: 'auto', // Always hidden to let child components handle scrolling
          marginLeft: isMobile ? '0px' : '0px',
          paddingTop: isMobile ? '56px' : '0px', // Add top padding for mobile topbar
          height: isMobile ? 'calc(100vh - 56px)' : '100vh',
          width: '100%'
        }}
      >
        {/* Show topbar only on mobile */}
        {isMobile && <TopBar />}
        <div style={{ padding: '0px' }}>
          <Outlet />
        </div>
      </div>
    </div>
  );
};

export default Layout;
