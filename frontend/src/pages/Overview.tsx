import { Link } from 'react-router-dom';
import { useAuth } from '../services/auth';
import { useResidentArea } from '../services/residentArea';
import { lazy, Suspense, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { CommunityServiceAlerts } from '../components/CommunityServiceAlerts';
import { Activity, ArrowRight, ArrowUpRight, CloudSun, Droplets, MapPin, Plus, ShieldCheck, Wind, Thermometer, RefreshCw, Sparkles, Zap, AlertTriangle, Map as MapIcon } from 'lucide-react';
import { getJson, type Weather } from '../services/api';
import './overview.css';
const MeterChart=lazy(()=>import('../components/MeterChart'));

type ConsumptionPoint = { current_date:string; consumption:number|null; is_estimated:boolean; data_source:string };
type Dashboard = {
  active_incidents:{value:number;updated_at:string};
  water:{month:string;consumption_kilolitres:number|null;reading_count:number;estimated_cost:number|null;observed_days?:number;estimated_days?:number;series?:ConsumptionPoint[];coverage_note?:string};
  energy:{month:string;consumption_kwh:number|null;reading_count:number;estimated_cost:number|null;observed_days?:number;estimated_days?:number;series?:ConsumptionPoint[];coverage_note?:string};
  recent_incidents:{id:number;category:string;title:string;location:string;created_at:string;status:string}[];
  official_alerts:unknown[]; timestamp:string;
};
type Risk = {category:string;explanation:string;risks:{risk:string;level:string;indicator_value:number|null;unit:string;indicator_name:string}[];engine_version:string;assessed_at:string};


function Stat({icon:Icon,label,value,unit,caption,tint}:{icon:React.ElementType;label:string;value:string;unit?:string;caption:string;tint:string}) { return <article className="stat-card"><div className="stat-top"><span className={`stat-icon ${tint}`}><Icon size={19}/></span></div><p className="stat-label">{label}</p><div className="stat-value">{value}<small>{unit}</small></div><p className="stat-caption">{caption}</p></article> }
function timeLabel(value:string){return new Date(value).toLocaleString('en-ZA',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});}
export function Overview() {
 const { user } = useAuth();
 const [briefingOpen, setBriefingOpen] = useState(false);
 const firstName = user?.full_name?.trim().split(/\s+/)[0] || 'neighbour';
 const area = useResidentArea();
 const place = area.data;
 const areaName = place?.municipality_name || (area.isPending ? 'Loading your area...' : 'Area unavailable');
 const hasCoordinates = place?.latitude != null && place?.longitude != null;
 const coords = hasCoordinates ? `latitude=${place!.latitude}&longitude=${place!.longitude}` : '';
 const mapUrl = hasCoordinates ? `https://www.openstreetmap.org/export/embed.html?bbox=${place!.longitude!-.11},${place!.latitude!-.09},${place!.longitude!+.11},${place!.latitude!+.09}&layer=mapnik&marker=${place!.latitude},${place!.longitude}` : '';

 const weather=useQuery({enabled:hasCoordinates,queryKey:['weather',coords],queryFn:()=>getJson<Weather>(`/climate/current?${coords}`)});
 const risk=useQuery({enabled:hasCoordinates,queryKey:['climate-risk',coords],queryFn:()=>getJson<Risk>(`/climate/risks?${coords}`)});
 const dashboard=useQuery({queryKey:['dashboard',user?.id],queryFn:()=>getJson<Dashboard>('/resident/dashboard')});
 const refresh=()=>{void Promise.all([...(hasCoordinates ? [weather.refetch(),risk.refetch()] : []),dashboard.refetch()]);};
 const w=weather.data, d=dashboard.data;
 const currentDay=new Date().toLocaleDateString('en-ZA',{weekday:'long',day:'numeric',month:'long',year:'numeric'}).toUpperCase();
 const riskColor=risk.data?.category==='high'?'critical':risk.data?.category==='moderate'?'watch':'low';
 const tips:string[]=[];
 if(w && w.current.temperature!=null && w.current.temperature>=35) tips.push('High temperatures are forecast. Drink water regularly and check on vulnerable neighbours.');
 if(w && w.daily.slice(0,2).some(day=>day.precipitation_sum!=null&&day.precipitation_sum>=25)) tips.push('Heavy rain is forecast. Avoid flooded roads and secure loose outdoor items.');
 if(!tips.length && d && d.water.reading_count===0) tips.push('Add a water reading or daily consumption to start tracking household use.');
 if(!tips.length && d && d.energy.reading_count===0) tips.push('Add an electricity reading or daily consumption to start tracking household use.');
 if(!tips.length) tips.push('No immediate household action is suggested by the data currently available. Check official municipal channels for service notices.');
 const climateValue=risk.isLoading?'…':risk.data?.category?risk.data.category[0].toUpperCase()+risk.data.category.slice(1):'—';
 const climateCaption=risk.isError?'Risk data unavailable':risk.data?`${risk.data.risks.map(i=>`${i.risk.replace('_',' ')}: ${i.level}`).join(' · ')} · Open-Meteo`:'Waiting for weather data';
 return <div className="content resident-overview">
  <div className="overview-topline"><span>{currentDay}</span><span><MapPin size={13}/>{place?.province || 'South Africa'}</span></div>
  <section className="community-hero">
    <div className="community-hero-copy"><span className="hero-kicker"><span/> YOUR COMMUNITY, CONNECTED</span><h1>A better day starts<br/>closer to home.</h1><p>Hello, {firstName}. Stay ahead of local changes, track your household, and help your community thrive.</p><div className="hero-actions"><Link className="hero-primary" to="/resident/report-incident"><Plus size={17}/> Report an issue <ArrowUpRight size={16}/></Link><a className="hero-secondary" href="#community-updates">Explore local updates <ArrowRight size={15}/></a></div><div className="hero-location"><MapPin size={16}/><div><strong>{areaName}</strong><span>{[place?.suburb,place?.province,'South Africa'].filter(Boolean).join(' / ')}</span></div><Link to="/resident/onboarding" aria-label="Change your selected area">Change area <ArrowUpRight size={13}/></Link></div></div>
    <div className="hero-neighbourhood" aria-hidden="true"><div className="neighbourhood-orbit orbit-one"/><div className="neighbourhood-orbit orbit-two"/><div className="neighbourhood-sun"/><div className="neighbourhood-ground"/><div className="city-building building-one"><i/><i/><i/><i/><i/><i/></div><div className="city-building building-two"><i/><i/><i/><i/><i/><i/><i/><i/></div><div className="city-house"><span/><i/><i/></div><div className="city-tree tree-one"/><div className="city-tree tree-two"/><div className="city-signal"><ShieldCheck size={22}/></div><span className="city-caption">Small actions. Stronger communities.</span></div>
  </section>
  <section className="overview-shortcuts" aria-label="Quick actions">
    {[{to:'/resident/water',icon:Droplets,title:'Track your water',text:'Record a reading',tone:'blue'},{to:'/resident/energy',icon:Zap,title:'Understand your energy',text:'See household usage',tone:'amber'},{to:'/resident/my-reports',icon:ShieldCheck,title:'Follow your reports',text:'Check progress & updates',tone:'green'},{to:'/resident/climate',icon:CloudSun,title:'Plan for the weather',text:'Explore local conditions',tone:'purple'}].map(({to,icon:Icon,title,text,tone})=><Link to={to} className="overview-shortcut" key={to}><span className={`shortcut-icon ${tone}`}><Icon size={21}/></span><div><strong>{title}</strong><span>{text}</span></div><ArrowUpRight size={16}/></Link>)}
  </section>
  <section className="daily-briefing"><div className="briefing-symbol"><Sparkles size={20}/></div><div><span className="briefing-kicker">YOUR DAILY FOCUS</span><h2>{tips[0]}</h2>{briefingOpen&&<div className="briefing-expanded"><p>{risk.data?.explanation || 'Follow official service notices for local disruptions. Add regular meter readings to understand how your household uses water and electricity.'}</p><Link to="/resident/climate">Explore conditions <ArrowRight size={14}/></Link></div>}</div><button className="briefing-toggle" aria-expanded={briefingOpen} onClick={()=>setBriefingOpen(!briefingOpen)}>{briefingOpen?'Show less':'View guidance'} <ArrowRight size={15}/></button></section>
  <div className="section-title" id="community-updates"><div><span className="overview-section-kicker">THE BIG PICTURE</span><h2>Your community at a glance</h2><p>Local conditions and your household activity, in one place.</p></div><button className="quiet-button" onClick={refresh} disabled={weather.isFetching||risk.isFetching||dashboard.isFetching}><RefreshCw size={14}/> Refresh</button></div>
  <CommunityServiceAlerts/>
  <section className="stats-grid">
   <Stat icon={Activity} label="Climate risk" value={climateValue} caption={climateCaption} tint="purple"/>
   <Stat icon={ShieldCheck} label="Active incidents" value={dashboard.isLoading?'…':d?String(d.active_incidents.value):'—'} caption={dashboard.isError?'Incident data unavailable':'Resident-submitted reports · unverified'} tint="rose"/>
   <Stat icon={Droplets} label="Water this month" value={dashboard.isLoading?'…':d?.water.consumption_kilolitres==null?'—':d.water.consumption_kilolitres.toFixed(3)} unit={d?.water.consumption_kilolitres==null?'':' kL'} caption={d?.water.reading_count?`${d.water.observed_days??d.water.reading_count} recorded / ${d.water.estimated_days??0} estimated days in ${d.water.month}`:'No readings recorded this month'} tint="blue"/>
   <Stat icon={Zap} label="Electricity this month" value={dashboard.isLoading?'…':d?.energy.consumption_kwh==null?'—':d.energy.consumption_kwh.toFixed(0)} unit={d?.energy.consumption_kwh==null?'':' kWh'} caption={d?.energy.reading_count?`${d.energy.observed_days??d.energy.reading_count} recorded / ${d.energy.estimated_days??0} estimated days in ${d.energy.month}`:'No readings recorded this month'} tint="amber"/>
  </section>
  <section className="dashboard-grid">
   <article className="panel weather-panel"><div className="panel-header"><div><div className="panel-title"><span className="panel-icon weather-icon"><CloudSun size={17}/></span><h3>Weather in {areaName}</h3></div><p>Open-Meteo · {w?`updated ${new Date(w.updated_at).toLocaleTimeString('en-ZA',{hour:'2-digit',minute:'2-digit'})}`:'public forecast'}</p></div><button className="quiet-button" onClick={()=>weather.refetch()} aria-label="Refresh weather"><RefreshCw size={14}/></button></div>
    {!hasCoordinates?<div className="inline-error">Local weather will be available when coordinates for your selected municipality are available.</div>:weather.isLoading?<div className="skeleton weather-skeleton"/>:weather.isError?<div className="inline-error"><CloudSun size={20}/><div><b>Weather unavailable</b><p>{(weather.error as Error).message}. Check the backend and internet connection.</p></div><button className="text-link" onClick={()=>weather.refetch()}>Retry</button></div>:w?<><div className="weather-main"><div><span className="temperature">{Math.round(w.current.temperature??0)}°</span><span className="weather-condition">{w.current.condition}</span><small>Modelled forecast · {w.updated_at}</small></div><div className="sun-art"><CloudSun size={54}/></div></div><div className="weather-metrics"><span><Droplets size={15}/><b>{w.current.humidity??'—'}%</b> humidity</span><span><Wind size={15}/><b>{w.current.wind_speed??'—'}</b> km/h wind</span><span><Thermometer size={15}/><b>{w.current.precipitation??'—'}</b> mm rain</span></div><div className="forecast-strip">{w.daily.slice(0,5).map((day,i)=><div key={day.date}><small>{i===0?'Today':new Date(day.date).toLocaleDateString('en-ZA',{weekday:'short'})}</small><CloudSun size={18}/><b>{day.high==null?'—':Math.round(day.high)}°</b></div>)}</div></>:<div className="inline-error">Weather response was empty.</div>}
   </article>
   <article className="panel risk-panel"><div className="panel-header"><div><div className="panel-title"><span className="panel-icon risk-icon"><Activity size={17}/></span><h3>Climate watch</h3></div><p>Weather threshold screening, not a service outage report</p></div><span className={`status-badge ${riskColor}`}>{risk.isLoading?'Checking':risk.data?.category??'Unavailable'}</span></div>{risk.data?<div className="risk-content"><div className="risk-indicators">{risk.data.risks.map(item=><div className="risk-indicator" key={item.risk}><span>{item.risk.replace('_',' ')}</span><b>{item.level}</b><small>{item.indicator_value!=null?`${item.indicator_value} ${item.unit}`:item.indicator_name}</small></div>)}</div><p className="risk-explanation">{risk.data.explanation}</p><a className="panel-link" href="/climate">Explore climate intelligence <ArrowRight size={15}/></a></div>:<div className="risk-empty"><div className="risk-ring"><CloudSun size={22}/></div><b>{!hasCoordinates?'Local assessment unavailable':risk.isError?'Assessment unavailable':'Assessing local conditions'}</b><p>{!hasCoordinates?'Coordinates for your selected municipality are needed to assess local conditions.':risk.isError?'Check the weather service and retry.':'Climate indicators will appear when the forecast is available.'}</p></div>}</article>
   <article className="panel trend-panel"><div className="panel-header"><div><div className="panel-title"><span className="panel-icon water-icon"><Droplets size={17}/></span><h3>Household water</h3></div><p>Daily consumption ? gaps mean no data; tooltips identify estimates</p></div><a className="quiet-button" href="/water">View details <ArrowUpRight size={14}/></a></div><Suspense fallback={<div className="skeleton weather-skeleton"/>}><MeterChart data={d?{items:d.water.series??[],count:d.water.reading_count}:undefined} color="#1687aa" unit="kL"/></Suspense></article>
   <article className="panel trend-panel"><div className="panel-header"><div><div className="panel-title"><span className="panel-icon energy-icon"><Zap size={17}/></span><h3>Electricity use</h3></div><p>Daily consumption ? gaps mean no data; tooltips identify estimates</p></div><a className="quiet-button" href="/energy">View details <ArrowUpRight size={14}/></a></div><Suspense fallback={<div className="skeleton weather-skeleton"/>}><MeterChart data={d?{items:d.energy.series??[],count:d.energy.reading_count}:undefined} color="#cf942d" unit="kWh"/></Suspense></article>
  </section>
  <div className="lower-grid"><article className="panel alerts-panel"><div className="panel-header"><div><div className="panel-title"><span className="panel-icon alert-icon"><AlertTriangle size={17}/></span><h3>Community updates</h3></div><p>Resident-submitted reports are unverified unless marked otherwise</p></div><a href="/resident/my-reports" className="quiet-button">My reports <ArrowRight size={14}/></a></div>
   {dashboard.isLoading?<div className="skeleton weather-skeleton"/>:dashboard.isError?<div className="empty-row"><span className="empty-row-icon"><AlertTriangle size={17}/></span><div><b>Report feed unavailable</b><p>Could not load local incident reports.</p></div></div>:d?.recent_incidents.length?d.recent_incidents.map(item=><div className="empty-row report-row" key={item.id}><span className="empty-row-icon"><AlertTriangle size={17}/></span><div><b>{item.title}</b><p>{item.category} · {item.location} · Resident report · {timeLabel(item.created_at)}</p></div><span className="report-status">{item.status.replace('_',' ')}</span></div>):<div className="empty-row"><span className="empty-row-icon"><ShieldCheck size={17}/></span><div><b>No resident reports yet</b><p>Reports submitted in this development environment will appear here.</p></div></div>}
   <div className="source-note"><ShieldCheck size={14}/> Municipal service notices appear above. Reports below are updates on your submitted issues.</div>
  </article>
  <article className="recommendation-card"><div className="rec-top"><span><Sparkles size={16}/></span><small>PREPAREDNESS TIPS · RULE BASED</small></div><h3>{tips[0]}</h3>{tips.slice(1).map(tip=><p key={tip}>{tip}</p>)}<a href="/climate">View local conditions <ArrowRight size={14}/></a><div className="rec-decoration"><MapIcon size={74}/></div></article></div>
  <section className="panel overview-map"><div className="panel-header"><div><div className="panel-title"><span className="panel-icon alert-icon"><MapPin size={17}/></span><h3>Community map</h3></div><p>{areaName} overview · OpenStreetMap</p></div><a href="/resident/community-map" className="quiet-button">Open map <ArrowUpRight size={14}/></a></div>{mapUrl ? <iframe title={`Map of ${areaName}`} loading="lazy" src={mapUrl}/> : <p className="map-caption">Map coordinates are not yet available for your selected municipality.</p>}<div className="map-caption">Map marker shows the selected area centre. Individual report pins require resident location coordinates.</div></section>
  <footer className="page-footer"><span>EcoPulse AI <i>·</i> Building resilient communities</span><span>Weather: Open-Meteo. Household data: resident-submitted local records.</span></footer>
 </div>
}
