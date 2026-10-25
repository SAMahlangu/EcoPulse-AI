import { useQuery } from '@tanstack/react-query';
import { getJson } from './api';
import { useAuth } from './auth';

type Area = { province_id: string | null; municipality_name: string | null; province: string | null; suburb: string | null; latitude: number | null; longitude: number | null };

export function useResidentArea() {
  const { user } = useAuth();
  return useQuery({
    queryKey: ['resident-area', user?.id],
    queryFn: () => getJson<{ profile: Area | null }>('/resident/onboarding'),
    enabled: !!user && user.role === 'resident',
    select: result => result.profile,
  });
}
