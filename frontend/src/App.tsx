import { useResidentArea } from './services/residentArea';
import { lazy, Suspense, useState, type ReactNode } from 'react';
import { Navigate, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { Bus, ChevronDown, CloudSun, Droplets, House, Lightbulb, Map, Menu, Plus, Settings, ShieldAlert, Sparkles, X, MapPin, ArrowUpRight, Leaf, Zap, CircleHelp } from 'lucide-react';
import { Overview } from './pages/Overview';
import { Assistant } from './pages/Assistant';
import { Climate } from './pages/Climate';
import { Water } from './pages/Water';
import { Energy } from './pages/Energy';
import { Incidents } from './pages/Incidents';
import './styles.css';
import { AcceptInvitationPage, AuthPage, LandingPage, LegalPage, MfaGate, RoleRedirect } from './pages/PublicPages';
import { useAuth, type Role } from './services/auth';
import './pages/workspace-extra.css';
import { NotificationsMenu } from './components/NotificationsMenu';
import ResidentReports from './pages/ResidentReports';
import './pages/resident-community.css';
import { ResidentOnboarding } from './pages/ResidentOnboarding';
import './pages/onboarding.css';
const WorkspacePortal=lazy(()=>import('./pages/PortalDashboards').then(module=>({default:module.WorkspacePortal})));

const ResidentCommunityMap=lazy(()=>import('./pages/ResidentCommunityMap'));
const CommunityResources=lazy(()=>import('./pages/CommunityResources'));
const EcoMoveDashboard=lazy(()=>import('./pages/EcoMoveDashboard'));

const navigation = [
  { label:'Overview', path:'/resident', icon:House }, { label:'Climate intelligence', path:'/resident/climate', icon:CloudSun },
  { label:'Water management', path:'/resident/water', icon:Droplets }, { label:'Energy management', path:'/resident/energy', icon:Zap },
  { label:'EcoMove AI', path:'/resident/ecomove', icon:Bus },
  { label:'Report an issue', path:'/resident/report-incident', icon:Plus }, { label:'Community map', path:'/resident/community-map', icon:Map },
  { label:'My reports', path:'/resident/my-reports', icon:ShieldAlert }, { label:'Community resources', path:'/resident/resources', icon:Leaf },
  { label:'AI assistant', path:'/resident/assistant', icon:Sparkles }, { label:'Profile & settings', path:'/resident/settings', icon:Settings },
];

function ResidentShell() {
  const area = useResidentArea();
  const [open,setOpen] = useState(false); const location=useLocation();
  const { user, logout } = useAuth();
  const title = navigation.find(n => n.path === location.pathname)?.label ?? 'EcoPulse AI';
  const initials=(user?.full_name??user?.email??'EP').split(/[ @]/).map(x=>x[0]).join('').slice(0,2).toUpperCase();
  return <div className="app-shell">
    <aside className={`sidebar ${open?'sidebar-open':''}`}><div className="brand"><img className="brand-logo" src="/ecopulse-ai-logo.png" alt="EcoPulse AI"/><button className="icon-button mobile-close" aria-label="Close navigation" onClick={()=>setOpen(false)}><X size={18}/></button></div>
      <div className="location-pill"><MapPin size={15}/><span><b>{area.data?.municipality_name || (area.isPending ? 'Loading area...' : 'Select your area')}</b><small>{area.data?.province || 'South Africa'}</small></span><ChevronDown size={15}/></div>
      <p className="nav-caption">YOUR COMMUNITY</p><nav>{navigation.map(item=><NavLink key={item.path} to={item.path} end={item.path==='/resident'} className={({isActive})=>`nav-link ${isActive?'active':''}`} onClick={()=>setOpen(false)}><item.icon size={18}/><span>{item.label}</span>{item.path==='/resident/report-incident'&&<span className="new-dot"/>}</NavLink>)}</nav>
      <div className="sidebar-bottom"><div className="help-card"><div className="help-icon"><CircleHelp size={17}/></div><b>Need a hand?</b><p>Find answers about EcoPulse and your community.</p><a href="/#contact">Contact EcoPulse <ArrowUpRight size={13}/></a></div><button className="profile-row" onClick={()=>void logout()} title="Sign out"><div className="avatar">{initials}</div><span><b>{user?.full_name||user?.email}</b><small>Resident account · Sign out</small></span><ChevronDown size={15}/></button></div>
    </aside>
    {open&&<button className="backdrop" aria-label="Close navigation" onClick={()=>setOpen(false)}/>}
    <main className="main-area"><header className="topbar"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={()=>setOpen(true)}><Menu size={20}/></button><div className="breadcrumb">Resident portal <span>/</span> <b>{title}</b></div><div className="top-actions"><div className="live-chip"><i/> Community portal</div><NotificationsMenu/><div className="top-avatar">{initials}</div></div></header>
      <Routes><Route path="onboarding" element={<ResidentOnboarding/>}/><Route index element={<Overview/>}/><Route path="assistant" element={<Assistant/>}/><Route path="climate" element={<Climate/>}/><Route path="water" element={<Water/>}/><Route path="energy" element={<Energy/>}/><Route path="ecomove" element={<Suspense fallback={<div className="auth-loading">Loading EcoMove map...</div>}><EcoMoveDashboard/></Suspense>}/><Route path="report-incident/*" element={<Incidents/>}/><Route path="incidents/*" element={<Incidents/>}/><Route path="my-reports" element={<ResidentReports/>}/><Route path="reports" element={<ResidentReports/>}/><Route path="community-map" element={<Suspense fallback={<div className="auth-loading">Loading your community map...</div>}><ResidentCommunityMap/></Suspense>}/><Route path="map" element={<Navigate to="/resident/community-map" replace/>}/><Route path="resources" element={<Suspense fallback={<div className="auth-loading">Loading community resources...</div>}><CommunityResources/></Suspense>}/><Route path="settings" element={<ResidentOnboarding/>}/><Route path="*" element={<SimplePage title={title}/>}/></Routes>
    </main>
  </div>
}

function SimplePage({title}:{title:string}) { return <div className="content"><div className="page-heading"><div><p className="eyebrow">YOUR COMMUNITY</p><h1>{title}</h1><p className="muted">Tools and updates for your household and community.</p></div></div><div className="empty-card"><div className="empty-icon"><Sparkles size={22}/></div><h2>{title} is getting ready</h2><p>No connected community data is available here yet. No sample or live data is shown until a verified source is connected.</p><a className="text-link" href="/#contact">Contact EcoPulse <ArrowUpRight size={15}/></a></div></div> }

function LegacyIncidentRedirect(){const location=useLocation();return <Navigate to={`/resident${location.pathname}`} replace/>;}
function AuthVerificationIssue({message}:{message:string}){const {refreshUser,logout}=useAuth();const [retrying,setRetrying]=useState(false);return <main className="auth-loading"><section role="alert" style={{maxWidth:440,padding:24,background:'#fff',borderRadius:12,boxShadow:'0 8px 30px #173b2210'}}><h1 style={{fontSize:20}}>We couldn’t verify your account</h1><p>{message}</p><p>Your sign-in is saved. Retry when the API or database is available.</p><button className="button-primary" disabled={retrying} onClick={async()=>{setRetrying(true);try{await refreshUser();}catch{}finally{setRetrying(false);}}}>{retrying?'Checking…':'Retry verification'}</button>{' '}<button className="button-outline" onClick={()=>void logout()}>Sign out</button></section></main>;}
function AuthRetryNotice({message}:{message:string}){const {refreshUser}=useAuth();const [retrying,setRetrying]=useState(false);return <div role="alert" style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:12,padding:'10px 16px',background:'#fff7e8',color:'#795d2d'}}><span>{message}</span><button className="button-outline" disabled={retrying} onClick={async()=>{setRetrying(true);try{await refreshUser();}catch{}finally{setRetrying(false);}}}>{retrying?'Checking…':'Retry verification'}</button></div>;}
function Protected({role,children}:{role:Role;children:ReactNode}){const {user,loading,error,hasSession}=useAuth();const location=useLocation();if(loading)return <div className="auth-loading">Checking your account…</div>;if(!user){if(hasSession&&error)return <AuthVerificationIssue message={error}/>;return <Navigate to="/login" replace/>;}if(user.role!==role)return <RoleRedirect/>;if(user.mfa_required)return <MfaGate/>;if(role==='resident'&&!user.onboarding_completed_at&&!location.pathname.endsWith('/onboarding'))return <Navigate to="/resident/onboarding" replace/>;return <>{error&&<AuthRetryNotice message={error} />}{children}</>;}
function AppRoutes(){return <Routes><Route path="/" element={<LandingPage/>}/><Route path="/login" element={<AuthPage mode="login"/>}/><Route path="/register" element={<AuthPage mode="register"/>}/><Route path="/forgot-password" element={<AuthPage mode="forgot-password"/>}/><Route path="/reset-password" element={<AuthPage mode="reset-password"/>}/><Route path="/accept-invitation" element={<AcceptInvitationPage/>}/><Route path="/privacy" element={<LegalPage kind="privacy"/>}/><Route path="/terms" element={<LegalPage kind="terms"/>}/><Route path="/resident/*" element={<Protected role="resident"><ResidentShell/></Protected>}/><Route path="/incidents/*" element={<LegacyIncidentRedirect/>}/><Route path="/municipal/*" element={<Protected role="municipal"><Suspense fallback={<div className="auth-loading">Loading municipal workspace…</div>}><WorkspacePortal role="municipal"/></Suspense></Protected>}/><Route path="/admin/*" element={<Protected role="admin"><Suspense fallback={<div className="auth-loading">Loading administration workspace…</div>}><WorkspacePortal role="admin"/></Suspense></Protected>}/>{['/climate','/water','/energy','/map','/reports','/resources','/assistant','/settings'].map(path=><Route key={path} path={path} element={<Navigate to={`/resident${path==='/'?'':path}`} replace/>}/>)}<Route path="*" element={<Navigate to="/" replace/>}/></Routes>;}
export default function App(){return <AppRoutes/>;}
