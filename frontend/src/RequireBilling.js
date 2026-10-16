import { Navigate } from 'react-router-dom';
import { useFeatures } from './hooks/useFeatures';

const RequireBilling = ({ children }) => {
  const { billing, loading } = useFeatures();
  if (loading) return null;
  if (!billing) return <Navigate to="/chat" replace />;
  return children;
};

export default RequireBilling;
