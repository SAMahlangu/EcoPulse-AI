// A navigation draft only: consumption never proves a leak or submits a report.
export function waterInvestigationPrefill(state: unknown): { title: string; description: string } | null {
  if (!state || typeof state !== 'object') return null;
  const candidate = (state as { waterInvestigation?: unknown }).waterInvestigation;
  if (!candidate || typeof candidate !== 'object') return null;
  const value = candidate as Record<string, unknown>;
  if (value.is_simulation !== false || typeof value.date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value.date)) return null;
  if (typeof value.litres !== 'number' || !Number.isFinite(value.litres) || value.litres < 0) return null;
  if (typeof value.target_litres !== 'number' || !Number.isFinite(value.target_litres) || value.target_litres <= 0) return null;
  return {
    title: 'Investigate possible water leak',
    description: `My household recorded ${value.litres.toLocaleString('en-ZA')} litres on ${value.date}, against a daily target of ${value.target_litres.toLocaleString('en-ZA')} litres. This consumption pattern does not confirm a leak. Please describe any visible damage or continuous water flow you observed before submitting.`,
  };
}
