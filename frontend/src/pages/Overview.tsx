import { lazy, Suspense } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Activity, ArrowRight, ArrowUpRight, CloudSun, Droplets, MapPin, Plus, ShieldCheck, Wind, Thermometer, RefreshCw, Sparkles, Zap, AlertTriangle, Map as MapIcon } from 'lucide-react';
import { getJson, type Weather } from '../services/api';
import './overview.css';
const MeterChart=lazy(()=>import('../components/MeterChart'));

type ReadingSeries = { items: { id:number; current:number; previous:number; current_date:string; consumption:number }[]; count:number };
type Dashboard = {
  active_incidents:{value:number;updated_at:string};
  water:{month:string;consumption_kilolitres:number|null;reading_count:number;estimated_cost:number|null};
  energy:{month:string;consumption_kwh:number|null;reading_count:number;estimated_cost:number|null};
  recent_incidents:{id:number;category:string;title:string;location:string;created_at:string;status:string}[];
  official_alerts:unknown[]; timestamp:string;
};
type Risk = {category:string;explanation:string;risks:{risk:string;level:string;indicator_value:number|null;unit:string;indicator_name:string}[];engine_version:string;assessed_at:string};
const coords='latitude=-25.7479&longitude=28.2293';

function Stat({icon:Icon,label,value,unit,caption,tint}:{icon:React.ElementType;label:string;value:string;unit?:string;caption:string;tint:string}) { return <article className="stat-card"><div className="stat-top"><span className={`stat-icon ${tint}`}><Icon size={19}/></span></div><p className="stat-label">{label}</p><div className="stat-value">{value}<small>{unit}</small></div><p className="stat-caption">{caption}</p></article> }
function timeLabel(value:string){return new Date(value).toLocaleString('en-ZA',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});}
export function Overview() {
 const weather=useQuery({queryKey:['weather','tshwane'],queryFn:()=>getJson<Weather>(`/climate/current?${coords}`)});
 const risk=useQuery({queryKey:['climate-risk','tshwane'],queryFn:()=>getJson<Risk>(`/climate/risks?${coords}`)});
 const dashboard=useQuery({queryKey:['dashboard'],queryFn:()=>getJson<Dashboard>('/resident/dashboard')});
 const water=useQuery({queryKey:['water','readings'],queryFn:()=>getJson<ReadingSeries>('/water/readings')});
 const energy=useQuery({queryKey:['energy','readings'],queryFn:()=>getJson<ReadingSeries>('/energy/readings')});
 const refresh=()=>{void Promise.all([weather.refetch(),risk.refetch(),dashboard.refetch(),water.refetch(),energy.refetch()]);};
 const w=weather.data, d=dashboard.data;
 const currentDay=new Date().toLocaleDateString('en-ZA',{weekday:'long',day:'numeric',month:'long',year:'numeric'}).toUpperCase();
 const riskColor=risk.data?.category==='high'?'critical':risk.data?.category==='moderate'?'watch':'low';
 const tips:string[]=[];
 if(w && w.current.temperature!=null && w.current.temperature>=35) tips.push('High temperatures are forecast. Drink water regularly and check on vulnerable neighbours.');
 if(w && w.daily.slice(0,2).some(day=>day.precipitation_sum!=null&&day.precipitation_sum>=25)) tips.push('Heavy rain is forecast. Avoid flooded roads and secure loose outdoor items.');
 if(!tips.length && (water.data?.count??0)===0) tips.push('Add a water meter reading to start tracking household use.');
 if(!tips.length && (energy.data?.count??0)===0) tips.push('Add an electricity meter reading to start tracking household use.');
 if(!tips.length) tips.push('No immediate household action is suggested by the data currently available. Check official municipal channels for service notices.');
 const climateValue=risk.isLoading?'…':risk.data?.category?risk.data.category[0].toUpperCase()+risk.data.category.slice(1):'—';
 const climateCaption=risk.isError?'Risk data unavailable':risk.data?`${risk.data.risks.map(i=>`${i.risk.replace('_',' ')}: ${i.level}`).join(' · ')} · Open-Meteo`:'Waiting for weather data';
 return <div className="content">
  <div className="page-heading overview-heading"><div><p className="eyebrow">{currentDay} <span className="eyebrow-dot">•</span> GAUTENG</p><h1>Community overview</h1><p className="muted">Local conditions and household updates for Tshwane.</p></div><a className="button-primary" href="/incidents/new"><Plus size={17}/> Report an issue</a></div>
  <section className="welcome-strip"><div className="welcome-mark"><MapPin size={19}/></div><div><b>Selected area</b><p>City of Tshwane <span>·</span> Gauteng, South Africa</p></div><span className="verified"><MapPin size={15}/> Area preview</span></section>
  <div className="section-title"><div><h2>Community at a glance</h2><p>Values use current weather or readings submitted to this development server.</p></div><button className="quiet-button" onClick={refresh}><RefreshCw size={14}/> Refresh</button></div>
  <section className="stats-grid">
   <Stat icon={Activity} label="Climate risk" value={climateValue} caption={climateCaption} tint="purple"/>
   <Stat icon={ShieldCheck} label="Active incidents" value={dashboard.isLoading?'…':d?String(d.active_incidents.value):'—'} caption={dashboard.isError?'Incident data unavailable':'Resident-submitted reports · unverified'} tint="rose"/>
   <Stat icon={Droplets} label="Water this month" value={dashboard.isLoading?'…':d?.water.consumption_kilolitres==null?'—':d.water.consumption_kilolitres.toFixed(3)} unit={d?.water.consumption_kilolitres==null?'':' kL'} caption={d?.water.reading_count?`${d.water.reading_count} reading(s) recorded in ${d.water.month}`:'No readings recorded this month'} tint="blue"/>
   <Stat icon={Zap} label="Electricity this month" value={dashboard.isLoading?'…':d?.energy.consumption_kwh==null?'—':d.energy.consumption_kwh.toFixed(0)} unit={d?.energy.consumption_kwh==null?'':' kWh'} caption={d?.energy.reading_count?`${d.energy.reading_count} reading(s) recorded in ${d.energy.month}`:'No readings recorded this month'} tint="amber"/>
  </section>
  <section className="dashboard-grid">
   <article className="panel weather-panel"><div className="panel-header"><div><div className="panel-title"><span className="panel-icon weather-icon"><CloudSun size={17}/></span><h3>Weather in Tshwane</h3></div><p>Open-Meteo · {w?`updated ${new Date(w.updated_at).toLocaleTimeString('en-ZA',{hour:'2-digit',minute:'2-digit'})}`:'public forecast'}</p></div><button className="quiet-button" onClick={()=>weather.refetch()} aria-label="Refresh weather"><RefreshCw size={14}/></button></div>
    {weather.isLoading?<div className="skeleton weather-skeleton"/>:weather.isError?<div className="inline-error"><CloudSun size={20}/><div><b>Weather unavailable</b><p>{(weather.error as Error).message}. Check the backend and internet connection.</p></div><button className="text-link" onClick={()=>weather.refetch()}>Retry</button></div>:w?<><div className="weather-main"><div><span className="temperature">{Math.round(w.current.temperature??0)}°</span><span className="weather-condition">{w.current.condition}</span><small>Modelled forecast · {w.updated_at}</small></div><div className="sun-art"><CloudSun size={54}/></div></div><div className="weather-metrics"><span><Droplets size={15}/><b>{w.current.humidity??'—'}%</b> humidity</span><span><Wind size={15}/><b>{w.current.wind_speed??'—'}</b> km/h wind</span><span><Thermometer size={15}/><b>{w.current.precipitation??'—'}</b> mm rain</span></div><div className="forecast-strip">{w.daily.slice(0,5).map((day,i)=><div key={day.date}><small>{i===0?'Today':new Date(day.date).toLocaleDateString('en-ZA',{weekday:'short'})}</small><CloudSun size={18}/><b>{day.high==null?'—':Math.round(day.high)}°</b></div>)}</div></>:<div className="inline-error">Weather response was empty.</div>}
   </article>
   <article className="panel risk-panel"><div className="panel-header"><div><div className="panel-title"><span className="panel-icon risk-icon"><Activity size={17}/></span><h3>Climate watch</h3></div><p>Weather threshold screening, not a service outage report</p></div><span className={`status-badge ${riskColor}`}>{risk.isLoading?'Checking':risk.data?.category??'Unavailable'}</span></div>{risk.data?<div className="risk-content"><div className="risk-indicators">{risk.data.risks.map(item=><div className="risk-indicator" key={item.risk}><span>{item.risk.replace('_',' ')}</span><b>{item.level}</b><small>{item.indicator_value!=null?`${item.indicator_value} ${item.unit}`:item.indicator_name}</small></div>)}</div><p className="risk-explanation">{risk.data.explanation}</p><a className="panel-link" href="/climate">Explore climate intelligence <ArrowRight size={15}/></a></div>:<div className="risk-empty"><div className="risk-ring"><CloudSun size={22}/></div><b>{risk.isError?'Assessment unavailable':'Assessing local conditions'}</b><p>{risk.isError?'Check the weather service and retry.':'Climate indicators will appear when the forecast is available.'}</p></div>}</article>
   <article className="panel trend-panel"><div className="panel-header"><div><div className="panel-title"><span className="panel-icon water-icon"><Droplets size={17}/></span><h3>Household water</h3></div><p>Recorded reading consumption</p></div><a className="quiet-button" href="/water">View details <ArrowUpRight size={14}/></a></div><Suspense fallback={<div className="skeleton weather-skeleton"/>}><MeterChart data={water.data} color="#1687aa" unit="kL"/></Suspense></article>
   <article className="panel trend-panel"><div className="panel-header"><div><div className="panel-title"><span className="panel-icon energy-icon"><Zap size={17}/></span><h3>Electricity use</h3></div><p>Recorded reading consumption</p></div><a className="quiet-button" href="/energy">View details <ArrowUpRight size={14}/></a></div><Suspense fallback={<div className="skeleton weather-skeleton"/>}><MeterChart data={energy.data} color="#cf942d" unit="kWh"/></Suspense></article>
  </section>
  <div className="lower-grid"><article className="panel alerts-panel"><div className="panel-header"><div><div className="panel-title"><span className="panel-icon alert-icon"><AlertTriangle size={17}/></span><h3>Community updates</h3></div><p>Resident-submitted reports are unverified unless marked otherwise</p></div><a href="/reports" className="quiet-button">My reports <ArrowRight size={14}/></a></div>
   {dashboard.isLoading?<div className="skeleton weather-skeleton"/>:dashboard.isError?<div className="empty-row"><span className="empty-row-icon"><AlertTriangle size={17}/></span><div><b>Report feed unavailable</b><p>Could not load local incident reports.</p></div></div>:d?.recent_incidents.length?d.recent_incidents.map(item=><div className="empty-row report-row" key={item.id}><span className="empty-row-icon"><AlertTriangle size={17}/></span><div><b>{item.title}</b><p>{item.category} · {item.location} · Resident report · {timeLabel(item.created_at)}</p></div><span className="report-status">{item.status.replace('_',' ')}</span></div>):<div className="empty-row"><span className="empty-row-icon"><ShieldCheck size={17}/></span><div><b>No resident reports yet</b><p>Reports submitted in this development environment will appear here.</p></div></div>}
   <div className="source-note"><ShieldCheck size={14}/> Official municipal alerts are not connected. Resident reports are not official notices.</div>
  </article>
  <article className="recommendation-card"><div className="rec-top"><span><Sparkles size={16}/></span><small>PREPAREDNESS TIPS · RULE BASED</small></div><h3>{tips[0]}</h3>{tips.slice(1).map(tip=><p key={tip}>{tip}</p>)}<a href="/climate">View local conditions <ArrowRight size={14}/></a><div className="rec-decoration"><MapIcon size={74}/></div></article></div>
  <section className="panel overview-map"><div className="panel-header"><div><div className="panel-title"><span className="panel-icon alert-icon"><MapPin size={17}/></span><h3>Community map</h3></div><p>City of Tshwane overview · OpenStreetMap</p></div><a href="/map" className="quiet-button">Open map <ArrowUpRight size={14}/></a></div><iframe title="Map of City of Tshwane" loading="lazy" src="https://www.openstreetmap.org/export/embed.html?bbox=28.12%2C-25.84%2C28.34%2C-25.66&layer=mapnik&marker=-25.7479%2C28.2293"/><div className="map-caption">Map marker shows the selected area centre. Individual report pins require resident location coordinates.</div></section>
  <footer className="page-footer"><span>EcoPulse AI <i>·</i> Building resilient communities</span><span>Weather: Open-Meteo. Household data: resident-submitted local records.</span></footer>
 </div>
}
