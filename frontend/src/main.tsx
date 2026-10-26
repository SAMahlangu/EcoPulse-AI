import React, { useState, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { AuthProvider, useAuth } from './services/auth';
import './styles.css';

function UserQueryClient({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { staleTime: 60_000, retry: 1 } } }));
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function IdentityQueryBoundary({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return <UserQueryClient key={user?.id ?? 'anonymous'}>{children}</UserQueryClient>;
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthProvider>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <IdentityQueryBoundary><App/></IdentityQueryBoundary>
      </BrowserRouter>
    </AuthProvider>
  </React.StrictMode>,
);
