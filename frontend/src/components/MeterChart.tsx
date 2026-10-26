import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Droplets, Plus, Zap } from 'lucide-react';
import { Link } from 'react-router-dom';

type Point = { current_date:string; consumption:number|null; is_estimated?:boolean; data_source?:string };
type ReadingSeries = { items:Point[]; count:number };
const dayLabel = (value:string) => new Date(`${value.slice(0,10)}T12:00:00`).toLocaleDateString('en-ZA',{day:'numeric',month:'short'});
export default function MeterChart({data,color,unit}:{data:ReadingSeries|undefined;color:string;unit:string}) {
 const points=[...(data?.items??[])].sort((a,b)=>a.current_date.localeCompare(b.current_date));
 if (!points.some(point=>point.consumption!==null)) return <div className="chart-placeholder"><div className={`chart-empty-mark ${unit==='kL'?'':'energy-mark'}`}>{unit==='kL'?<Droplets size={20}/>:<Zap size={20}/>}</div><b>Start your household trend</b><p>Add daily consumption or meter readings to see your use over time.</p><Link className="button-outline" to={unit==='kL'?'/resident/water':'/resident/energy'}><Plus size={15}/> Add a reading</Link></div>;
 return <div className="chart-area"><ResponsiveContainer width="100%" height="100%"><AreaChart data={points} margin={{top:12,right:4,left:-20,bottom:0}}><defs><linearGradient id={`fill-${unit}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity={0.22}/><stop offset="95%" stopColor={color} stopOpacity={0.01}/></linearGradient></defs><CartesianGrid vertical={false} stroke="#eef1ee"/><XAxis dataKey="current_date" tickFormatter={dayLabel} tick={{fontSize:8,fill:'#98a39a'}} axisLine={false} tickLine={false}/><YAxis tick={{fontSize:8,fill:'#98a39a'}} axisLine={false} tickLine={false}/><Tooltip formatter={(value:number,_name:string,item:{payload?:Point})=>[`${value} ${unit}`,item.payload?.is_estimated?'Estimated daily use':'Recorded daily use']} labelFormatter={value=>dayLabel(String(value))}/><Area type="monotone" dataKey="consumption" connectNulls={false} stroke={color} strokeWidth={2} fill={`url(#fill-${unit})`}/></AreaChart></ResponsiveContainer></div>;
}
