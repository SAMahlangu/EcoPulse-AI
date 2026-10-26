import { CircleMarker, MapContainer, Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import EcoMoveTileLayer from './ecomove/EcoMoveTileLayer';
import 'leaflet/dist/leaflet.css';

export type ClimateMarker={latitude:number;longitude:number;label:string;layer:string;value:number|string|null;unit?:string|null;source:string;data_kind:string;updated_at:string};
export type IncidentMarker={id:number;title:string;category:string;location:string;latitude:number;longitude:number;created_at:string;status:string};
const greenMarker=L.divIcon({className:'ecopulse-map-marker',html:'<span></span>',iconSize:[18,18],iconAnchor:[9,9]});
function Recenter({latitude,longitude}:{latitude:number;longitude:number}){const map=useMap();map.setView([latitude,longitude],Math.max(map.getZoom(),9));return null;}
export default function ClimateMap({marker,incidents=[]}:{marker?:ClimateMarker;incidents?:IncidentMarker[]}){
 const latitude=marker?.latitude??-25.7479,longitude=marker?.longitude??28.2293;
 return <div className="climate-map-frame"><MapContainer center={[latitude,longitude]} zoom={9} scrollWheelZoom className="climate-leaflet"><EcoMoveTileLayer/><Recenter latitude={latitude} longitude={longitude}/>{marker&&<Marker position={[latitude,longitude]} icon={greenMarker}><Popup><b>{marker.label}</b><br/>{String(marker.value??'—')} {marker.unit??''}<br/><small>{marker.layer.replace('_',' ')} · {marker.data_kind}</small><br/><small>{marker.source}</small></Popup></Marker>}{incidents.map(item=><CircleMarker key={item.id} center={[item.latitude,item.longitude]} radius={7} pathOptions={{color:'#b7433d',fillColor:'#d96b5e',fillOpacity:.9}}><Popup><b>{item.title}</b><br/>{item.category} · resident report<br/>{item.location}<br/><small>{item.created_at} · {item.status}</small></Popup></CircleMarker>)}</MapContainer></div>;
}
