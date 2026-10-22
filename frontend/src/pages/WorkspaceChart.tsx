import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
type Item=Record<string,any>;
export default function WorkspaceChart({rows,label}:{rows:Item[];label:string}){
  const data=(rows??[]).map(x=>({...x,[label]:String(x[label]??'').replace(/_/g,' ')}));
  return data.length?<div className="workspace-chart"><ResponsiveContainer width="100%" height={Math.max(180,data.length*32)}><BarChart data={data} layout="vertical" margin={{top:4,right:18,bottom:4,left:8}}><CartesianGrid stroke="#edf1ed" horizontal={false}/><XAxis type="number" allowDecimals={false} axisLine={false} tickLine={false} tick={{fontSize:9,fill:'#89958c'}}/><YAxis type="category" dataKey={label} width={130} axisLine={false} tickLine={false} tick={{fontSize:9,fill:'#66756a'}}/><Tooltip cursor={{fill:'#f5f8f5'}}/><Bar dataKey="count" fill="#438a57" radius={[0,5,5,0]} maxBarSize={16}/></BarChart></ResponsiveContainer></div>:<div className="workspace-chart-empty">No records to chart.</div>;
}
