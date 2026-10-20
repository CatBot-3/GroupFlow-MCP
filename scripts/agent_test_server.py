"""Separate synthetic demo for an independent agent; all provider calls disabled."""
import json
import argparse
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
parser = argparse.ArgumentParser()
parser.add_argument('--name', choices=['agent-test', 'agent-verified', 'ui-verified'], default='agent-test')
parser.add_argument('--port',type=int,default=18987)
parser.add_argument('--sim-port',type=int,default=18988)
args = parser.parse_args()
out = root / 'eval/runs/agent-test'
out.mkdir(parents=True, exist_ok=True)
os.environ['AGENT_PROVIDER'] = 'none'
os.environ['GROUPFLOW_CHECKINS'] = 'off'
os.environ['GROUPFLOW_CHAT_DB'] = str(out / (args.name + '.chat.db'))
from groupflow.world import load_world
from groupflow.__main__ import main
world = load_world()
(out / 'synthetic_identities.json').write_text(json.dumps({p.id: {'token': p.token, 'passcode': p.passcode} for p in world.people}, indent=2), encoding='utf8')
raise SystemExit(main(['demo', '--db', str(out / (args.name + '.db')), '--port', str(args.port), '--sim-port', str(args.sim_port)]))
