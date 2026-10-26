import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { latLngBounds } from 'leaflet';
import { CircleMarker, MapContainer, Popup, useMap } from 'react-leaflet';
import { getJson } from '../services/api';
import EcoMoveTileLayer from '../components/ecomove/EcoMoveTileLayer';
import 'leaflet/dist/leaflet.css';

type Item=Record<string,any>;
type Point=[number,number];

function FitReportedPoints({points}:{points:Point[]}){
  const map=useMap();
  const pointKey=points.map(point=>point.join(',')).join(';');
  useEffect(()=>{
    if(points.length)map.fitBounds(latLngBounds(points),{padding:[32,32],maxZoom:13});
  },[map,pointKey]);
  return null;
}

export default function MunicipalMap({items}:{items:Item[]}){
  const [active,setActive]=useState<string|null>(null);
  const details=useQuery({queryKey:['mapped-report',active],queryFn:()=>getJson<{media:{url:string|null}[]}>(`/municipal/incidents/${active}`),enabled:active!==null});
  const mappedItems=useMemo(()=>items.filter(item=>{
    const latitude=Number(item.latitude),longitude=Number(item.longitude);
    return Number.isFinite(latitude)&&latitude>=-90&&latitude<=90&&Number.isFinite(longitude)&&longitude>=-180&&longitude<=180;
  }),[items]);
  const points=useMemo(()=>mappedItems.map(item=>[Number(item.latitude),Number(item.longitude)] as Point),[mappedItems]);
  const first=points[0];

  return <div className="municipal-incident-map">
    <MapContainer center={first??[-29,24]} zoom={first?11:5} scrollWheelZoom>
      <EcoMoveTileLayer/>
      <FitReportedPoints points={points}/>
      {mappedItems.map(item=><CircleMarker key={item.id} center={[Number(item.latitude),Number(item.longitude)]} radius={8} eventHandlers={{click:()=>setActive(item.id)}} pathOptions={{color:item.priority==='urgent'||item.priority==='high'?'#c44d45':'#277849',fillOpacity:.78}}>
        <Popup>
          <b>{item.title}</b><br/>
          <b>Location:</b> {item.location_text||'Location description unavailable'}<br/>
          {item.category} · {item.status}<br/>
          {new Date(item.created_at).toLocaleDateString()}<br/>
          {item.public_reference}
          {active===item.id&&details.data?.media[0]?.url&&<img src={details.data.media[0].url} alt="Report attachment" style={{width:180,display:'block',marginTop:8}}/>}
          <br/><Link to={`/municipal/incidents?report=${item.id}`}>Open full report</Link>
        </Popup>
      </CircleMarker>)}
    </MapContainer>
  </div>;
}
