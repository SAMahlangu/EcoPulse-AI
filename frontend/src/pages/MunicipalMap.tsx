import { CircleMarker, MapContainer, Popup, TileLayer } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
type Item=Record<string,any>;
export default function MunicipalMap({items}:{items:Item[]}){
  const first=items[0];const center:[number,number]=first?[Number(first.latitude),Number(first.longitude)]:[-29.0,24.0];
  return <div className="municipal-incident-map"><MapContainer center={center} zoom={first?11:5} scrollWheelZoom><TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"/>{items.map(x=><CircleMarker key={x.id} center={[Number(x.latitude),Number(x.longitude)]} radius={8} pathOptions={{color:x.priority==='urgent'||x.priority==='high'?'#c44d45':'#277849',fillOpacity:.78}}><Popup><b>{x.title}</b><br/>{x.category} · {x.status}<br/>{new Date(x.created_at).toLocaleDateString()}</Popup></CircleMarker>)}</MapContainer></div>;
}
