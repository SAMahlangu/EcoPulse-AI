import { useResidentArea } from './services/residentArea';
import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react';
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { Bus, ChevronDown, CloudSun, Droplets, House, Lightbulb, LogOut, Map, Menu, Plus, Settings, ShieldAlert, Sparkles, X, MapPin, ArrowUpRight, Leaf, Zap, CircleHelp } from 'lucide-react';
import { Overview } from './pages/Overview';
import { Assistant } from './pages/Assistant';
import { Climate } from './pages/Climate';
import { Water } from './pages/Water';
import { Energy } from './pages/Energy';
import { Incidents } from './pages/Incidents';
import './styles.css';
import { AcceptInvitationPage, AuthPage, LandingPage, LegalPage, RoleRedirect } from './pages/PublicPages';
import { AuthProvider, useAuth, type Role } from './services/auth';
import { getJson } from './services/api';
import { useQuery } from '@tanstack/react-query';
import './pages/workspace-extra.css';
import { NotificationsMenu } from './components/NotificationsMenu';
import './components/account-menu.css';
import ResidentReportSharing from './components/ResidentReportSharing';
import { ResidentOnboarding } from './pages/ResidentOnboarding';
import './pages/onboarding.css';
const WorkspacePortal=lazy(()=>import('./pages/PortalDashboards').then(module=>({default:module.WorkspacePortal})));

const EcoMoveDashboard=lazy(()=>import('./pages/EcoMoveDashboard'));
const ResidentCommunityMap=lazy(()=>import('./pages/ResidentCommunityMap'));
const ResidentResourcesMap=lazy(()=>import('./pages/ResidentResourcesMap'));

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
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement>(null);
  const accountTriggerRef = useRef<HTMLButtonElement>(null);
  const firstAccountItemRef = useRef<HTMLAnchorElement>(null);
  const title = navigation.find(n => n.path === location.pathname)?.label ?? 'EcoPulse AI';
  const initials=(user?.full_name??user?.email??'EP').split(/[ @]/).map(x=>x[0]).join('').slice(0,2).toUpperCase();
  const accountName = user?.full_name || user?.email || 'EcoPulse resident';

  useEffect(() => {
    if (!accountMenuOpen) return;
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (!accountMenuRef.current?.contains(event.target as Node)) setAccountMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setAccountMenuOpen(false);
        accountTriggerRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', closeOnOutsideClick);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsideClick);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [accountMenuOpen]);

  useEffect(() => {
    if (accountMenuOpen) firstAccountItemRef.current?.focus();
  }, [accountMenuOpen]);

  const handleAccountMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Tab') {
      setAccountMenuOpen(false);
      return;
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const items = Array.from(accountMenuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    if (!items.length) return;
    event.preventDefault();
    const current = items.indexOf(document.activeElement as HTMLElement);
    const next = event.key === 'Home' ? 0
      : event.key === 'End' ? items.length - 1
      : current < 0 ? (event.key === 'ArrowDown' ? 0 : items.length - 1)
      : (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[next].focus();
  };

  return <div className="app-shell">
    <aside className={`sidebar ${open?'sidebar-open':''}`}><div className="brand"><img className="brand-logo" src="/ecopulse-ai-logo.png" alt="EcoPulse AI"/><button className="icon-button mobile-close" aria-label="Close navigation" onClick={()=>setOpen(false)}><X size={18}/></button></div>
      <div className="location-pill"><MapPin size={15}/><span><b>{area.data?.municipality_name || (area.isPending ? 'Loading area...' : 'Select your area')}</b><small>{area.data?.province || 'South Africa'}</small></span><ChevronDown size={15}/></div>
      <p className="nav-caption">YOUR COMMUNITY</p><nav>{navigation.map(item=><NavLink key={item.path} to={item.path} end={item.path==='/resident'} className={({isActive})=>`nav-link ${isActive?'active':''}`} onClick={()=>setOpen(false)}><item.icon size={18}/><span>{item.label}</span>{item.path==='/resident/report-incident'&&<span className="new-dot"/>}</NavLink>)}</nav>
      <div className="sidebar-bottom"><div className="help-card"><div className="help-icon"><CircleHelp size={17}/></div><b>Need a hand?</b><p>Find answers about EcoPulse and your community.</p><a href="#contact">Contact EcoPulse <ArrowUpRight size={13}/></a></div></div>
    </aside>
    {open&&<button className="backdrop" aria-label="Close navigation" onClick={()=>setOpen(false)}/>}
    <main className="main-area"><header className="topbar"><button className="icon-button mobile-menu" aria-label="Open navigation" onClick={()=>setOpen(true)}><Menu size={20}/></button><div className="breadcrumb">Resident portal <span>/</span> <b>{title}</b></div><div className="top-actions"><div className="live-chip"><i/> Community portal</div><NotificationsMenu/><div className="account-menu" ref={accountMenuRef} onKeyDown={handleAccountMenuKeyDown}><button ref={accountTriggerRef} className="top-avatar account-menu-trigger" type="button" aria-label={`Account menu for ${accountName}`} aria-haspopup="menu" aria-expanded={accountMenuOpen} aria-controls="resident-account-menu" onClick={()=>setAccountMenuOpen(value=>!value)}>{initials}</button>{accountMenuOpen&&<div className="account-popover" id="resident-account-menu" role="menu" aria-label="Account options"><div className="account-popover-header" role="presentation"><span className="account-popover-avatar">{initials}</span><span className="account-popover-details"><b>{accountName}</b>{user?.email&&user.email!==accountName&&<small>{user.email}</small>}<small className="account-popover-role">Resident account</small></span></div><div className="account-popover-divider" role="separator"/><NavLink ref={firstAccountItemRef} className="account-popover-item" role="menuitem" to="/resident/settings" onClick={()=>setAccountMenuOpen(false)}><Settings size={16}/>Profile &amp; settings</NavLink><button className="account-popover-item account-signout" type="button" role="menuitem" onClick={()=>{setAccountMenuOpen(false);void logout();}}><LogOut size={16}/>Sign out</button></div>}</div></div></header>
      <Routes><Route path="onboarding" element={<ResidentOnboarding/>}/><Route index element={<Overview/>}/><Route path="assistant" element={<Assistant/>}/><Route path="climate" element={<Climate/>}/><Route path="water" element={<Water/>}/><Route path="energy" element={<Energy/>}/><Route path="ecomove" element={<Suspense fallback={<div className="auth-loading">Loading EcoMove map...</div>}><EcoMoveDashboard/></Suspense>}/><Route path="report-incident/*" element={<Incidents/>}/><Route path="incidents/*" element={<Incidents/>}/><Route path="my-reports" element={<ResidentReports/>}/>{/* The map stays resident-authenticated; reports are private or explicitly shared as approximate pins. */}<Route path="community-map/*" element={<Suspense fallback={<div className="auth-loading">Loading your community map...</div>}><ResidentCommunityMap/></Suspense>}/><Route path="resources" element={<Suspense fallback={<div className="auth-loading">Loading South African community resources…</div>}><ResidentResourcesMap/></Suspense>}/><Route path="settings" element={<ResidentOnboarding/>}/><Route path="*" element={<SimplePage title={title}/>}/></Routes>
    </main>
  </div>
}

function SimplePage({title}:{title:string}) { return <div className="content"><div className="page-heading"><div><p className="eyebrow">YOUR COMMUNITY</p><h1>{title}</h1><p className="muted">Tools and updates for your household and community.</p></div></div><div className="empty-card"><div className="empty-icon"><Sparkles size={22}/></div><h2>{title} is getting ready</h2><p>No connected community data is available here yet. No sample or live data is shown until a verified source is connected.</p><a className="text-link" href="#contact">Contact EcoPulse <ArrowUpRight size={15}/></a></div></div> }

function ResidentReports(){const reports=useQuery({queryKey:['incidents','mine'],refetchInterval:15000,queryFn:()=>getJson<{items:Array<{id:string|number;public_reference?:string;municipality_name?:string;department_name?:string;title:string;category:string;status:string;created_at:string;location:string;latitude:number|null;longitude:number|null;community_map_consent?:boolean}>}>('/incidents')});const [selected,setSelected]=useState<string|number|null>(null);const details=useQuery({queryKey:['incidents','mine',selected],refetchInterval:15000,queryFn:()=>getJson<{incident:{title:string;description:string;status:string;created_at:string;public_reference?:string;municipality_name?:string;department_name?:string};updates:Array<{status:string;message:string;created_at:string}>;media:Array<{content_type:string;url:string|null}>}>(`/incidents/${selected}`),enabled:selected!==null});return <div className="content"><div className="page-heading"><div><p className="eyebrow">YOUR COMMUNITY</p><h1>My reports</h1><p className="muted">View your report updates and manage optional map sharing for each report.</p></div></div>{reports.isPending?<div className="weather-skeleton skeleton"/>:reports.isError?<div className="empty-card"><h2>Reports unavailable</h2><p>{(reports.error as Error).message}</p></div>:reports.data.items.length?<div className="panel report-list">{reports.data.items.map(row=><article key={row.id}><div><b>{row.title}</b><small>{row.public_reference??row.id} {row.municipality_name} {row.department_name} · {row.category} · {row.location} · {new Date(row.created_at).toLocaleDateString()}</small></div><span className={`status-pill ${row.status}`}>{row.status.replace(/_/g,' ')}</span><button className="button-outline" onClick={()=>setSelected(row.id)}>Updates</button>{row.latitude!=null&&row.longitude!=null?<ResidentReportSharing reportId={row.id} shared={!!row.community_map_consent}/>:<small className="report-sharing-unavailable">Add a map location to share a pin</small>}</article>)}</div>:<div className="empty-card"><h2>No reports yet</h2><p>When you submit an incident, it will appear here.</p><NavLink className="button-primary" to="/resident/report-incident">Report a local issue</NavLink></div>}{selected!==null&&<div className="dialog-scrim" onMouseDown={e=>{if(e.target===e.currentTarget)setSelected(null);}}><section className="incident-dialog"><button className="dialog-close" onClick={()=>setSelected(null)}><X size={18}/></button>{details.isPending?<p>Loading report updates…</p>:details.isError?<p>{(details.error as Error).message}</p>:<><p className="eyebrow">REPORT UPDATE HISTORY</p><h2>{details.data.incident.title}</h2><p>{details.data.incident.public_reference}</p><p>{details.data.incident.description}</p><p>{details.data.incident.municipality_name} / {details.data.incident.department_name}</p><p aria-label="Report stages">Submitted / Under review / Assigned / In progress / Resolved</p>{details.data.media?.filter(x=>x.url).map((x,i)=><img className="municipal-incident-photo" key={i} src={x.url!} alt="Your incident attachment"/>)}<div className="resident-update-list">{details.data.updates.length?details.data.updates.map((x,i)=><article key={i}><span>{x.status||'submitted'}</span><p>{x.message}</p><small>{new Date(x.created_at).toLocaleString()}</small></article>):<p>No public updates have been recorded yet.</p>}</div></>}</section></div>}</div>}

function LegacyIncidentRedirect(){const location=useLocation();return <Navigate to={`/resident${location.pathname}`} replace/>;}
function AuthVerificationIssue({message}:{message:string}){const {refreshUser,logout}=useAuth();const [retrying,setRetrying]=useState(false);return <main className="auth-loading"><section role="alert" style={{maxWidth:440,padding:24,background:'#fff',borderRadius:12,boxShadow:'0 8px 30px #173b2210'}}><h1 style={{fontSize:20}}>We couldn’t verify your account</h1><p>{message}</p><p>Your sign-in is saved. Retry when the API or database is available.</p><button className="button-primary" disabled={retrying} onClick={async()=>{setRetrying(true);try{await refreshUser();}catch{}finally{setRetrying(false);}}}>{retrying?'Checking…':'Retry verification'}</button>{' '}<button className="button-outline" onClick={()=>void logout()}>Sign out</button></section></main>;}
function AuthRetryNotice({message}:{message:string}){const {refreshUser}=useAuth();const [retrying,setRetrying]=useState(false);return <div role="alert" style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:12,padding:'10px 16px',background:'#fff7e8',color:'#795d2d'}}><span>{message}</span><button className="button-outline" disabled={retrying} onClick={async()=>{setRetrying(true);try{await refreshUser();}catch{}finally{setRetrying(false);}}}>{retrying?'Checking…':'Retry verification'}</button></div>;}
function Protected({role,children}:{role:Role;children:ReactNode}){const {user,loading,error,hasSession}=useAuth();const location=useLocation();if(loading)return <div className="auth-loading">Checking your account…</div>;if(!user){if(hasSession&&error)return <AuthVerificationIssue message={error}/>;return <Navigate to="/login" replace/>;}if(user.role!==role)return <RoleRedirect/>;if(role==='resident'&&!user.onboarding_completed_at&&!location.pathname.endsWith('/onboarding'))return <Navigate to="/resident/onboarding" replace/>;return <>{error&&<AuthRetryNotice message={error} />}{children}</>;}
function EmailConfirmationRedirect(){
  const location=useLocation();const navigate=useNavigate();const {logout}=useAuth();const handledHash=useRef<string|null>(null);
  useLayoutEffect(()=>{
    if(!location.hash||handledHash.current===location.hash)return;
    const fragment=new URLSearchParams(location.hash.slice(1));const type=fragment.get('type');
    const hasError=fragment.has('error')||fragment.has('error_code')||fragment.has('error_description');
    const isSignupConfirmation=type==='signup'||((location.pathname==='/'||location.pathname==='/login')&&hasError);
    if(!isSignupConfirmation)return;
    handledHash.current=location.hash;
    // Confirmation links place session tokens in the URL fragment; revoke and remove them before login.
    void logout(fragment.get('access_token')??undefined);
    navigate(`/login?email_confirmation=${hasError?'failed':'confirmed'}`,{replace:true});
  },[location.hash,location.pathname,logout,navigate]);
  return null;
}
function AppRoutes(){return <><EmailConfirmationRedirect/><Routes><Route path="/" element={<LandingPage/>}/><Route path="/login" element={<AuthPage mode="login"/>}/><Route path="/register" element={<AuthPage mode="register"/>}/><Route path="/forgot-password" element={<AuthPage mode="forgot-password"/>}/><Route path="/reset-password" element={<AuthPage mode="reset-password"/>}/><Route path="/accept-invitation" element={<AcceptInvitationPage/>}/><Route path="/privacy" element={<LegalPage kind="privacy"/>}/><Route path="/terms" element={<LegalPage kind="terms"/>}/><Route path="/resident/*" element={<Protected role="resident"><ResidentShell/></Protected>}/><Route path="/incidents/*" element={<LegacyIncidentRedirect/>}/><Route path="/municipal/*" element={<Protected role="municipal"><Suspense fallback={<div className="auth-loading">Loading municipal workspace…</div>}><WorkspacePortal role="municipal"/></Suspense></Protected>}/><Route path="/admin/*" element={<Protected role="admin"><Suspense fallback={<div className="auth-loading">Loading administration workspace…</div>}><WorkspacePortal role="admin"/></Suspense></Protected>}/>{['/climate','/water','/energy','/map','/community-map','/reports','/resources','/assistant','/settings'].map(path=><Route key={path} path={path} element={<Navigate to={`/resident${path==='/'?'':path}`} replace/>}/>)}<Route path="*" element={<Navigate to="/" replace/>}/></Routes></>;}
export default function App(){return <AuthProvider><AppRoutes/></AuthProvider>;}
