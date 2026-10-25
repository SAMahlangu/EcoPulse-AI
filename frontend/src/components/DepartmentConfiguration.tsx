import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getJson, postJson, putJson } from '../services/api';
import { MunicipalitySelect, ProvinceSelect } from './geography/GeographySelects';

type Department = {
  id: string;
  municipality_id: string;
  municipality: string;
  department_name: string;
  department_code: string;
  is_triage: boolean;
};

const categories = ['water', 'electricity', 'roads', 'waste', 'stormwater', 'environmental', 'other'];

export default function DepartmentConfiguration() {
  const qc = useQueryClient();
  const config = useQuery({
    queryKey: ['department-configuration'],
    queryFn: () => getJson<{
      departments: Department[];
      memberships: { user_id: string; department_id: string; role: string; is_active: boolean }[];
      routing: { category: string; department_id: string }[];
    }>('/infrastructure/configuration'),
  });
  const employees = useQuery({
    queryKey: ['department-employees'],
    queryFn: () => getJson<{ items: { id: string; full_name: string; role: string }[] }>('/admin/users?limit=100'),
  });

  const [provinceId, setProvinceId] = useState('');
  const [municipality, setMunicipality] = useState('');
  const [name, setName] = useState('');
  const [code, setCode] = useState('');
  const [department, setDepartment] = useState('');
  const [employee, setEmployee] = useState('');
  const [role, setRole] = useState('employee');
  const [category, setCategory] = useState('water');

  const mutate = useMutation({
    mutationFn: ({ kind }: { kind: string }) => kind === 'create'
      ? postJson('/infrastructure/departments', { municipality_id: municipality, department_name: name, department_code: code })
      : kind === 'member'
        ? putJson(`/infrastructure/departments/${department}/memberships`, { user_id: employee, role, is_active: true })
        : putJson(`/infrastructure/routing/${municipality}`, { category, department_id: department }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['department-configuration'] }),
  });

  const departments = (config.data?.departments ?? []).filter(item => item.municipality_id === municipality);

  return <section className="workspace-panel">
    <div className="workspace-panel-head"><div>
      <h2>Infrastructure departments and routing</h2>
      <p>Choose a province, then a municipality. Assign employees explicitly; unrouted reports enter municipal triage.</p>
    </div></div>
    {config.isPending ? <p>Loading departments…</p> : config.isError ? <p role="alert">{(config.error as Error).message}</p> : <>
      <div className="notice-fields">
        <label>Province<ProvinceSelect value={provinceId} onChange={id => {
          setProvinceId(id);
          setMunicipality('');
          setDepartment('');
        }}/></label>
        <label>Municipality<MunicipalitySelect provinceId={provinceId} value={municipality} infrastructure onChange={id => {
          setMunicipality(id);
          setDepartment('');
        }}/></label>
        <label>New department name<input value={name} onChange={event => setName(event.target.value)} placeholder="Water and Sanitation"/></label>
        <label>Department code<input value={code} onChange={event => setCode(event.target.value.toUpperCase())} placeholder="WATER"/></label>
        <button className="button-outline" disabled={!municipality || name.trim().length < 3 || !code || mutate.isPending} onClick={() => mutate.mutate({ kind: 'create' })}>Create department</button>
        <label>Department<select value={department} onChange={event => setDepartment(event.target.value)}>
          <option value="">Choose department</option>
          {departments.map(item => <option value={item.id} key={item.id}>{item.department_name}</option>)}
        </select></label>
        <label>Municipal employee<select value={employee} onChange={event => setEmployee(event.target.value)}>
          <option value="">Choose employee</option>
          {employees.data?.items.filter(item => item.role === 'municipal').map(item => <option key={item.id} value={item.id}>{item.full_name}</option>)}
        </select></label>
        <label>Department responsibility<select value={role} onChange={event => setRole(event.target.value)}>
          <option value="employee">Employee</option><option value="manager">Manager</option>
        </select></label>
        <button className="button-outline" disabled={!department || !employee || mutate.isPending} onClick={() => mutate.mutate({ kind: 'member' })}>Grant department access</button>
        <label>Service category<select value={category} onChange={event => setCategory(event.target.value)}>
          {categories.map(item => <option key={item}>{item}</option>)}
        </select></label>
        <button className="button-primary" disabled={!municipality || !department || mutate.isPending} onClick={() => mutate.mutate({ kind: 'route' })}>Save category routing</button>
      </div>
      <div className="notice-list">{departments.map(item => <article key={item.id}>
        <b>{item.department_name}</b>
        <p>{item.is_triage ? 'Default municipal triage' : (config.data.routing.filter(route => route.department_id === item.id).map(route => route.category).join(', ') || 'No category routes configured')}</p>
        <small>{config.data.memberships.filter(member => member.department_id === item.id && member.is_active).length} authorised employees</small>
      </article>)}</div>
      {municipality && departments.length === 0 && <p>No departments have been configured for this municipality yet. Apply the default department migration or create one above.</p>}
    </>}
    {mutate.error && <p className="form-error">{(mutate.error as Error).message}</p>}
    {employees.isError && <p className="form-error">Could not load municipal employees: {(employees.error as Error).message}</p>}
    {mutate.isSuccess && <p role="status">Configuration saved.</p>}
  </section>;
}
