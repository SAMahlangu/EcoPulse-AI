import { useState } from 'react';
import { Droplets, FlaskConical } from 'lucide-react';
import { CommunityServiceAlerts } from '../components/CommunityServiceAlerts';
import { WaterIntelligence } from '../components/water/WaterIntelligence';
import './resident-community.css';
import './water.css';

export function Water() {
  const [tab, setTab] = useState<'actual' | 'simulation'>('actual');
  return <div className="content household-water">
    <div className="page-heading"><div><p className="eyebrow">HOUSEHOLD SERVICES</p><h1>Water management</h1><p className="muted">Record household use, investigate changes and follow municipal supply updates.</p></div></div>
    <CommunityServiceAlerts service="water"/>
    <div className="resident-workspace-tabs" role="tablist" aria-label="Water workspace">
      {(['actual', 'simulation'] as const).map(value => <button key={value} role="tab" id={`water-${value}-tab`} aria-controls="water-workspace" aria-selected={tab === value} tabIndex={tab === value ? 0 : -1} className={tab === value ? 'active' : ''} data-testid={`water-${value}-tab`} onClick={() => setTab(value)} onKeyDown={event => { if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) { event.preventDefault(); const next = event.key === 'Home' ? 'actual' : event.key === 'End' ? 'simulation' : tab === 'actual' ? 'simulation' : 'actual'; setTab(next); document.getElementById(`water-${next}-tab`)?.focus(); } }}>{value === 'actual' ? <Droplets size={16}/> : <FlaskConical size={16}/>}{value === 'actual' ? 'Daily water consumption' : 'Water simulation'}</button>)}
    </div>
    <div id="water-workspace" role="tabpanel" aria-labelledby={`water-${tab}-tab`}><WaterIntelligence key={tab} simulation={tab === 'simulation'}/></div>
  </div>;
}
