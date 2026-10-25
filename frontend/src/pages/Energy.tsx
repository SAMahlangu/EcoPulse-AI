import { useState } from 'react';
import { FlaskConical, Zap } from 'lucide-react';
import { CommunityServiceAlerts } from '../components/CommunityServiceAlerts';
import { EnergyIntelligence } from '../components/energy/EnergyIntelligence';
import './energy.css';

export function Energy() {
  const [tab, setTab] = useState<'actual' | 'simulation'>('actual');
  return <div className="content household-energy">
    <div className="page-heading"><div><p className="eyebrow">HOUSEHOLD SERVICES</p><h1>Energy management</h1><p className="muted">Understand electricity use, investigate changes and find practical ways to save.</p></div></div>
    <CommunityServiceAlerts service="electricity"/>
    <div className="energy-tabs" role="tablist" aria-label="Energy workspace">
      <button role="tab" id="energy-actual-tab" aria-controls="energy-workspace" aria-selected={tab === 'actual'} className={tab === 'actual' ? 'active' : ''} data-testid="energy-actual-tab" onClick={() => setTab('actual')}><Zap size={16}/>Daily Energy Consumption</button>
      <button role="tab" id="energy-simulation-tab" aria-controls="energy-workspace" aria-selected={tab === 'simulation'} className={tab === 'simulation' ? 'active' : ''} data-testid="energy-simulation-tab" onClick={() => setTab('simulation')}><FlaskConical size={16}/>Energy Consumption Simulation</button>
    </div>
    <div id="energy-workspace" role="tabpanel" aria-labelledby={`energy-${tab}-tab`}><EnergyIntelligence key={tab} simulation={tab === 'simulation'}/></div>
  </div>;
}
