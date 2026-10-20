"""Run the local shared-server acceptance suite without a model provider."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out',default='eval/runs/2026-10-18-shared-workload/verification')
args=parser.parse_args()
base=(root/args.out).resolve()
if not base.is_relative_to(root/'eval'/'runs'):
    parser.error('--out must stay inside this repository eval/runs directory')
out=base/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+uuid.uuid4().hex[:8])
out.mkdir(parents=True,exist_ok=False)
env=dict(os.environ,AGENT_PROVIDER='none',GROUPFLOW_CHECKINS='off',SHARED_WORKLOAD_EVIDENCE_DIR=str(out/'workflow'))
files=sorted(str(p.relative_to(root)) for p in (root/'tests'/'mcp').glob('test_shared_*.py'))
command=[sys.executable,'-u','-m','pytest',*files,'-q','-p','no:cacheprovider',
         '--basetemp='+str(out/'tmp'),'--junitxml='+str(out/'junit.xml')]
with (out/'pytest.log').open('w',encoding='utf8') as log:
    result=subprocess.run(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
summary={'exit_code':result.returncode,'test_files':files,'external_model_api_used':False,'shared_world_per_workload':True,
         'artifacts':str(out.relative_to(root))}
if (out/'junit.xml').exists():
    suites=ET.parse(out/'junit.xml').getroot()
    for key in ('tests','failures','errors','skipped'):
        summary[key]=sum(int(s.get(key,0)) for s in suites.iter('testsuite'))
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf8')
print(json.dumps(summary))
raise SystemExit(result.returncode)
