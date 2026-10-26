import { Link } from 'react-router-dom';
import { ArrowUpRight, CloudSun, Droplets, MapPin, Zap } from 'lucide-react';
import { CommunityServiceAlerts } from '../components/CommunityServiceAlerts';
import { useResidentArea } from '../services/residentArea';
import './resident-community.css';

export default function CommunityResources() {
  const area = useResidentArea();
  return <div className="content resident-community"><div className="page-heading"><div><p className="eyebrow">YOUR COMMUNITY</p><h1>Community resources</h1><p className="muted">Published service information and practical tools for {area.data?.municipality_name ?? 'your selected municipality'}.</p></div></div>
    <CommunityServiceAlerts/>
    <section className="resident-resource-grid" aria-label="Available EcoPulse resources">{[
      { to: '/resident/report-incident', title: 'Report a local issue', text: 'Send the location and optional photo to the responsible municipal department.', Icon: MapPin },
      { to: '/resident/water', title: 'Household water', text: 'Record actual readings, set a target and investigate unusual consumption.', Icon: Droplets },
      { to: '/resident/energy', title: 'Household electricity', text: 'Track consumption, appliances and known prepaid balances.', Icon: Zap },
      { to: '/resident/climate', title: 'Weather and climate', text: 'View sourced forecasts and EcoPulse hazard screening for your area.', Icon: CloudSun },
    ].map(({ to, title, text, Icon }) => <Link className="panel resident-resource-card" key={to} to={to}><Icon size={23}/><h2>{title}</h2><p>{text}</p><span>Open resource <ArrowUpRight size={15}/></span></Link>)}</section>
    <section className="panel resident-resource-note"><h2>Local contacts and facilities</h2><p>A verified directory of local contacts and facilities has not been connected for this area. Municipal updates above are provided by authorised officials; no contact numbers or facility availability are inferred.</p><Link className="text-link" to="/resident/my-reports">Follow an existing report</Link><a className="text-link" href="/#contact">Contact EcoPulse</a></section>
  </div>;
}
