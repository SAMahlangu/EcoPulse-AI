import { useMutation, useQueryClient } from '@tanstack/react-query';
import { putJson } from '../services/api';
import './resident-report-sharing.css';

export default function ResidentReportSharing({ reportId, shared }: { reportId: string | number; shared: boolean }) {
  const queryClient = useQueryClient();
  const change = useMutation({
    mutationFn: () => putJson<{ shared: boolean }>(`/resident/community-reports/${encodeURIComponent(String(reportId))}/sharing`, { share: !shared }),
    onSuccess: result => {
      queryClient.setQueryData<{ items: Array<Record<string, unknown>> }>(['incidents', 'mine'], current => current && ({
        ...current,
        items: current.items.map(item => item.id === reportId ? { ...item, community_map_consent: result.shared } : item),
      }));
      void queryClient.invalidateQueries({ queryKey: ['resident-community-resources'] });
    },
  });
  return <div className="report-map-sharing">
    <button className="button-outline" type="button" disabled={change.isPending} onClick={() => change.mutate()}>
      {change.isPending ? 'Updating…' : shared ? 'Remove map pin' : 'Share approximate pin'}
    </button>
    {change.error && <small role="alert">{(change.error as Error).message}</small>}
  </div>;
}
