"""Bounded interval-scheduler scaling smoke benchmark, independent of route crossover."""
import argparse
import json
import platform
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from groupflow.projects.models import ProjectConfig
from groupflow.projects.scheduler import solve


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=Path('docs/project_benchmark.json'))
    p.add_argument('--budget',type=float,default=3)
    args=p.parse_args()
    results=[]
    for size in (10,25,50,100,200,400):
        work=[{'id':str(i),'title':f'Unit {i}','owner_id':f'person{i%10}','spec':{'not_before':'2026-10-19','due':'2026-11-27',
              'duration_minutes':60,'resources':{'lab':1} if i%4==0 else {},'depends_on':[str(i-10)] if i>=10 else []}} for i in range(size)]
        result=solve(work,ProjectConfig(),'America/Toronto','2026-10-19',42,{'lab':3},[],budget_s=args.budget)
        result['schedule_count']=len(result.pop('schedule'))
        results.append({'work_units':size,**result})
        print(size,result['status'],result.get('optimal'),result.get('ms'),flush=True)
    data={'python':platform.python_version(),'machine':platform.machine(),'budget_s':args.budget,'fixture':'10 owners, lab capacity3, chains of length N/10, 42 calendar days',
          'purpose':'scaling smoke check; NOT an enumeration crossover measurement','results':results}
    args.out.write_text(json.dumps(data,indent=2),encoding='utf-8')


if __name__=='__main__': main()
