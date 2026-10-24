import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import { httpsOrigin, securityPolicyPlugin } from './security-policy.mjs';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const plugins = [react()];

  // A release bundle must not silently point at localhost or send sessions over HTTP.
  if (mode === 'production') {
    const apiOrigin = httpsOrigin(env.VITE_API_URL, 'VITE_API_URL');
    const supabaseOrigin = httpsOrigin(env.VITE_SUPABASE_URL, 'VITE_SUPABASE_URL');
    plugins.push(securityPolicyPlugin({ apiOrigin, supabaseOrigin }));
  }

  return { plugins, server: { port: 5173 } };
});
