"""Command line entry point.

    python -m groupflow demo        GroupFlow server (8787) and the Alexa+ simulator (8788) together
    python -m groupflow server      only the MCP server (with the human confirmation API)
    python -m groupflow sim         only the simulator (connects to GROUPFLOW_MCP_URL)
    python -m groupflow reset       reset the demo data (a group such as family or friends, friends:<stage>, or all)
    python -m groupflow check       show the configuration (never prints secrets)
    python -m groupflow eval        run the LLM evaluation scenarios
    python -m groupflow evidence    capture the MCP handshake for docs/PROTOCOL_EVIDENCE.md
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

STAGES = ["committed", "draft", "proposed", "awaiting", "degraded"]


def _quiet_logs() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    for noisy in ("httpx", "httpx2", "mcp", "mcp.server", "mcp.client", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _server_settings(args: argparse.Namespace):
    from groupflow.core.settings import ServerSettings

    s = ServerSettings()
    if getattr(args, "port", None):
        s = replace(s, port=args.port)
    if getattr(args, "db", None):
        s = replace(s, db_path=args.db)
    if getattr(args, "world", None):
        s = replace(s, world_path=str(Path(args.world).resolve()))
    return s


def _build_runtime(args: argparse.Namespace):
    from groupflow.runtime import build_runtime, log_call

    settings = _server_settings(args)
    if getattr(args, "fresh", False) and settings.db_path != ":memory:":
        for suffix in ("", "-wal", "-shm"):
            Path(settings.db_path + suffix).unlink(missing_ok=True)
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    stages = {"friday_dinner": args.stage} if getattr(args, "stage", None) else None
    from groupflow.core.db import SchemaMismatch

    try:
        return settings, build_runtime(settings, stages=stages, on_call=log_call)
    except SchemaMismatch as err:
        print(f"[groupflow] {err}", file=sys.stderr)
        raise SystemExit(1) from None


def cmd_server(args: argparse.Namespace) -> int:
    from groupflow.core.serve import run_app

    settings, rt = _build_runtime(args)
    print(f"[groupflow] MCP server on http://{settings.host}:{settings.port}/mcp (protocol 2025-11-25, Streamable HTTP)", flush=True)
    print(f"[groupflow] human confirmation API on http://{settings.host}:{settings.port}/human", flush=True)
    print(f"[groupflow] database {settings.db_path}", flush=True)
    run_app(rt.app, host=settings.host, port=settings.port)
    return 0


def _sim_banner(sim, app_provider: dict) -> None:
    ui = f"http://localhost:{sim.port}" if Path(sim.web_dist, "index.html").is_file() else "http://localhost:5173 (run the web dev server: cd web && npm run dev)"
    print(f"[simulator] Alexa+ simulator API on http://{sim.host}:{sim.port}", flush=True)
    if app_provider.get("ready"):
        print(f"[simulator] LLM: {app_provider['label']} {app_provider['model']}", flush=True)
    else:
        print(f"[simulator] LLM not configured: {app_provider.get('reason')}", flush=True)
    print(f"[simulator] MCP server: {sim.mcp_url}", flush=True)
    print(f"[simulator] open {ui}", flush=True)


def cmd_sim(args: argparse.Namespace) -> int:
    from groupflow.core.serve import run_app

    from groupflow.core.settings import SimSettings
    from groupflow.sim.app import build_sim_app

    sim = SimSettings()
    if args.sim_port:
        sim = replace(sim, port=args.sim_port)
    app = build_sim_app(sim, world_path=getattr(args, "world", None))
    _sim_banner(sim, _provider_info(app))
    run_app(app, host=sim.host, port=sim.port)
    return 0


def _provider_info(app) -> dict:
    return app.state.agent.provider_info


def cmd_demo(args: argparse.Namespace) -> int:
    from groupflow.core.serve import run_app, serve_in_background
    from groupflow.core.settings import SimSettings
    from groupflow.sim.app import build_sim_app

    settings, rt = _build_runtime(args)
    try:
        bg = serve_in_background(rt.app, host=settings.host, port=settings.port)
    except OSError as err:
        print(f"[groupflow] could not start the server on port {settings.port}: {err}. Is another copy already running?", file=sys.stderr)
        return 1
    print(f"[groupflow] MCP server on {bg.base}/mcp, human confirmation API on {bg.base}/human", flush=True)
    sim = SimSettings()
    sim = replace(sim, mcp_url=f"{bg.base}/mcp", port=args.sim_port or sim.port)
    app = build_sim_app(sim, world_path=settings.world_path)
    _sim_banner(sim, _provider_info(app))
    try:
        run_app(app, host=sim.host, port=sim.port)
    finally:
        bg.stop()
        rt.db.close()
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    import httpx

    from groupflow.core.settings import ServerSettings, SimSettings

    scenario = None if args.scenario in (None, "all") else args.scenario
    sim = SimSettings()
    try:
        r = httpx.post(f"{sim.server_base}/debug/reset", params={"scenario": scenario} if scenario else None,
                       headers={"x-groupflow-admin": ServerSettings().admin_key}, timeout=10)
        if r.status_code == 200:
            print(f"[groupflow] reset {args.scenario or 'all'} on the running server")
            return 0
        print(f"[groupflow] the running server refused the reset ({r.status_code})", file=sys.stderr)
        return 1
    except httpx.HTTPError:
        pass
    # Not running: rebuild the database file directly.
    args.fresh = scenario is None
    _settings, rt = _build_runtime(args)
    if scenario:
        rt.services.on_reset(scenario)
    rt.db.close()
    print(f"[groupflow] reset {args.scenario or 'all'} in the database file (server was not running)")
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    from groupflow.core.settings import REPO_ROOT, LlmSettings, ServerSettings, SimSettings
    from groupflow.sim.llm.factory import create_provider

    s, sim, llm = ServerSettings(), SimSettings(), LlmSettings()
    p = create_provider(llm)
    print(f"python        {sys.version.split()[0]}")
    print(f".env          {'found' if (REPO_ROOT / '.env').is_file() else 'not found (optional)'}")
    print(f"server        http://{s.host}:{s.port}/mcp   db {s.db_path}")
    print(f"simulator     http://{sim.host}:{sim.port}   web {'built' if Path(sim.web_dist, 'index.html').is_file() else 'not built'}")
    from groupflow.templates import load_templates
    from groupflow.world import load_world

    w = load_world(s.world_path)
    print(f"templates     {', '.join(load_templates())}")
    print(f"demo world    {len(w.people)} people, {len(w.groups)} groups, {len(w.plans)} plans ({s.world_path})")
    print(f"provider      {llm.provider}: {'ready, ' + p.label + ' ' + p.model if p.id != 'none' else 'NOT READY - ' + str(p.reason)}")
    if llm.provider == "deepseek":
        print(f"DeepSeek key  {'present' if llm.deepseek_api_key else 'missing'}")
    from groupflow.repair.cpsat import available
    from groupflow.repair.msvc import describe

    print(f"solver        exact enumeration; CP-SAT {'available' if available() else 'not available'}" + (f" ({describe()})" if os.name == "nt" else ""))
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from groupflow.evals.runner import main as eval_main

    return eval_main(args.rest)


def cmd_evidence(args: argparse.Namespace) -> int:
    from groupflow.evidence import main as evidence_main

    return evidence_main(args.rest)


PASSTHROUGH = {"eval", "evidence"}


def main(argv: list[str] | None = None) -> int:
    if os.name == "nt":  # readable output in a default Windows console
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="groupflow", description="GroupFlow: an Alexa+ MCP add-on for group plans.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def server_opts(p: argparse.ArgumentParser) -> None:
        p.add_argument("--port", type=int, help="server port (default 8787 or GROUPFLOW_PORT)")
        p.add_argument("--db", help="SQLite file (default data/groupflow.db or GROUPFLOW_DB)")
        p.add_argument("--world", help="Demo world YAML; use demo/enterprise.yaml with a separate --db for the six-week project demo")
        p.add_argument("--stage", choices=STAGES, default=None, help="starting stage of the Friday dinner when seeding (default committed)")
        p.add_argument("--fresh", action="store_true", help="start from a fresh database")

    p_demo = sub.add_parser("demo", help="server and simulator together")
    server_opts(p_demo)
    p_demo.add_argument("--sim-port", type=int)
    p_demo.set_defaults(fn=cmd_demo)
    p_server = sub.add_parser("server", help="only the MCP server")
    server_opts(p_server)
    p_server.set_defaults(fn=cmd_server)
    p_sim = sub.add_parser("sim", help="only the simulator")
    p_sim.add_argument("--sim-port", type=int)
    p_sim.add_argument("--world", help="Use the same demo world as the MCP server")
    p_sim.set_defaults(fn=cmd_sim)
    p_reset = sub.add_parser("reset", help="reset demo data")
    p_reset.add_argument("scenario", nargs="?", help="a group (family, friends, cottage, team, study), friends:<stage>, or all (default)")
    server_opts(p_reset)
    p_reset.set_defaults(fn=cmd_reset)
    sub.add_parser("check", help="show configuration").set_defaults(fn=cmd_check)
    sub.add_parser("eval", help="LLM evaluation (python -m groupflow eval --help)", add_help=False).set_defaults(fn=cmd_eval)
    sub.add_parser("evidence", help="protocol evidence (python -m groupflow evidence --help)", add_help=False).set_defaults(fn=cmd_evidence)

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in PASSTHROUGH:  # these commands parse their own options
        args = parser.parse_args(argv[:1])
        args.rest = argv[1:]
    else:
        args = parser.parse_args(argv)
    _quiet_logs()
    try:
        return args.fn(args) or 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
