import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {RunDetail} from './src/pages/Simulations';
import {api} from './src/services/api';
import data from './tests/fixtures/behavior-review.json';
import './src/styles.css';
let legacy=false;
const simulation={id:data.analysis.simulation_id,status:'completed',mode:'test',agent:'custom',progress:1,started_at:null,finished_at:null,scenario:{name:'Synthetic braking review',seed:0,map:'Town03',duration_s:1.2,fixed_delta_seconds:.05}};
api.getSimulation=async()=>({simulation,revision:1,job:null,result:{simulation,metrics:data.analysis.stored_safety_metrics,measured_duration_s:1.15,telemetry_frames:24,artifacts:[],runtime:null,driving_outcome:data.analysis.driving_outcome}} as any);
api.getBehaviorAnalysis=async()=>{ const a=structuredClone(data.analysis); if(legacy){a.events=[];a.coverage.total_response_events=0;a.coverage.applied_decisions=0;a.coverage.decision_coverage='unavailable';a.driving_outcome=null;a.execution_status='failed';} return a as any; };
api.getDecisions=async(id,{offset=0,limit=10}={})=>({simulation_id:id,available:!legacy,coverage:legacy?'unavailable':'complete',total:legacy?0:data.records.length,applied:legacy?0:data.records.length,transitions:data.records.length,offset,limit,reset:null,recording_artifact_index:null,records:legacy?[]:data.records.slice(offset,offset+limit),telemetry:legacy?[]:data.telemetry.slice(offset,Math.min(offset+limit,data.records.length)+1)} as any);
api.getTelemetry=async(id,{offset=0,limit=200}={})=>({offset,limit,total:data.telemetry.length,frames:data.telemetry.slice(offset,offset+limit)} as any);
function Preview(){const[narrow,setNarrow]=useState(false),[version,setVersion]=useState(0);return <main style={{maxWidth:narrow?390:1280,margin:'24px auto',padding:16}}><label>Preview width <select onChange={e=>setNarrow(e.target.value==='mobile')}><option>desktop</option><option>mobile</option></select></label><label> Recorded example <select onChange={e=>{legacy=e.target.value==='legacy';setVersion(v=>v+1);}}><option>complete</option><option>legacy</option></select></label><RunDetail key={version} id={simulation.id} active/></main>;}
createRoot(document.getElementById('root')!).render(<Preview/>);
