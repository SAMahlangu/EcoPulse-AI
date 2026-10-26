import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Restricted Windows checkouts may deny esbuild access to parent directories
// while it bundles vite.config.ts; use this minimal local development config.
export default defineConfig({
  configFile: false,
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true },
});
