import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { checkSession } from './utils/authStorage';

const RedirectBasedOnAuth = () => {
  const navigate = useNavigate();

  useEffect(() => {
    checkSession().then((valid) => {
      navigate(valid ? '/chat' : '/login');
    });
  }, [navigate]);

  return null;
};

export default RedirectBasedOnAuth;
