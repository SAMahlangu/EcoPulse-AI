import assert from 'node:assert/strict';
import { httpsOrigin, securityPolicyPlugin } from '../security-policy.mjs';

assert.equal(httpsOrigin('https://api.example.test/api/v1', 'VITE_API_URL'), 'https://api.example.test');
assert.throws(() => httpsOrigin('http://localhost:8000/api/v1', 'VITE_API_URL'), /must use HTTPS/);
assert.throws(() => httpsOrigin('https://localhost:8000/api/v1', 'VITE_API_URL'), /public service address/);
assert.throws(() => httpsOrigin('https://api.example.test/api/v1?token=never', 'VITE_API_URL'), /must not contain/);

const plugin = securityPolicyPlugin({
  apiOrigin: 'https://api.example.test',
  supabaseOrigin: 'https://project.supabase.co',
});
const html = plugin.transformIndexHtml('<!doctype html><html><head><title>EcoPulse</title></head></html>');
assert.match(html, /Content-Security-Policy/);
assert.match(html, /script-src 'self'/);
assert.match(html, /https:\/\/api\.example\.test/);
assert.match(html, /https:\/\/project\.supabase\.co/);
assert.match(html, /object-src 'none'/);
console.log('Security policy checks passed.');
