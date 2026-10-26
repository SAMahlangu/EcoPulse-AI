import { useEffect, useState } from 'react';
import { MapContainer, Marker, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import { getJson } from '../services/api';
import EcoMoveTileLayer from './ecomove/EcoMoveTileLayer';
import 'leaflet/dist/leaflet.css';

const icon=L.divIcon({html:'<span style="display:block;background:#166b51;border:3px solid white;border-radius:50%;width:22px;height:22px;box-shadow:0 2px 8px #0006"></span>',className:'',iconSize:[22,22],iconAnchor:[11,11]});
function Position({position,onChange}:{position:[number,number]|null;onChange:(lat:number,lon:number)=>void}){
  const map=useMap();
  useMapEvents({click:event=>onChange(event.latlng.lat,event.latlng.lng)});
  useEffect(()=>{if(position)map.setView(position,Math.max(map.getZoom(),13));},[position?.[0],position?.[1],map]);
  return position?<Marker icon={icon} position={position} draggable eventHandlers={{dragend:event=>{const p=event.target.getLatLng();onChange(p.lat,p.lng);}}}/>:null;
}
export default function IncidentLocationPicker({latitude,longitude,onChange}:{latitude:string;longitude:string;onChange:(lat:number,lon:number)=>void}){
  const [search,setSearch]=useState('');const [results,setResults]=useState<{name:string;latitude:number;longitude:number}[]>([]);const [message,setMessage]=useState('');const [busy,setBusy]=useState(false);
  async function find(){setBusy(true);setMessage('');try{const data=await getJson<{items:typeof results}>(`/climate/locations?search=${encodeURIComponent(search)}`);setResults(data.items);if(!data.items.length)setMessage('No locations found. Select the point on the map.');}catch{setMessage('Search is unavailable. You can still select a point on the map.');}finally{setBusy(false);}}
  const position:[number,number]|null=latitude&&longitude?[Number(latitude),Number(longitude)]:null;
  return <div className="incident-location-picker"><label className="wizard-label">Search a town or suburb<input value={search} onChange={e=>setSearch(e.target.value)} placeholder="For example, Pretoria"/></label><button type="button" className="button-outline" disabled={search.trim().length<2||busy} onClick={()=>void find()}>{busy?'Searching…':'Search location'}</button>{results.map((item,index)=><button key={index} type="button" className="table-action" onClick={()=>{onChange(item.latitude,item.longitude);setResults([]);}}>{item.name}</button>)}{message&&<p role="status">{message}</p>}<div style={{height:300,marginTop:12}}><MapContainer center={position??[-29,25]} zoom={position?13:5} style={{height:'100%',borderRadius:12}}><EcoMoveTileLayer/><Position position={position} onChange={onChange}/></MapContainer></div><small>Click the map or drag the marker to the issue. Check the position before submitting.</small></div>;
}
