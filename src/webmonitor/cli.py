"""Explicit process entry points; only implemented commands are registered."""
import argparse
import asyncio
import sys
import getpass


def main() -> None:
    parser = argparse.ArgumentParser(prog="webmonitor")
    commands = parser.add_subparsers(dest="command", required=True)
    api = commands.add_parser("api", help="Run the HTTP API")
    api.add_argument("--host", default="0.0.0.0")
    api.add_argument("--port", default=8000, type=int)
    commands.add_parser("init-db", help="Initialize a new compatible development database")
    admin = commands.add_parser("admin-create", help="Create an administrator with an interactive password")
    admin.add_argument("--email", required=True)
    invite = commands.add_parser("invite-create", help="Issue an allowlisted single-use invitation")
    invite.add_argument("--email", required=True)
    invite.add_argument("--ttl-hours", type=int, default=24)
    for name in ("scheduler", "http-worker", "browser-worker", "agent-worker", "mailer"):
        commands.add_parser(name)
    args = parser.parse_args()
    if args.command == "api":
        import uvicorn
        from webmonitor.config import get_settings
        trusted_proxy = get_settings().trusted_proxy_ip
        uvicorn.run("webmonitor.api.app:create_app", factory=True, host=args.host,
                    port=args.port, proxy_headers=trusted_proxy is not None,
                    forwarded_allow_ips=trusted_proxy or "", access_log=False)
    elif args.command == "init-db":
        from webmonitor.db.initialize import initialize_database
        try:
            asyncio.run(initialize_database())
        except Exception as exc:
            print(f"Initialization failed ({type(exc).__name__}); no automatic migration performed", file=sys.stderr)
            raise SystemExit(1) from None
        print("Database initialized")
    elif args.command in {"admin-create", "invite-create"}:
        from webmonitor.api.errors import DomainError
        from webmonitor.db.session import get_engine, get_session_factory
        from webmonitor.services.accounts import admin_create, invite_create
        password = None
        if args.command == "admin-create":
            password = getpass.getpass("Password (12–128 characters): ")
            if password != getpass.getpass("Confirm password: "):
                parser.error("Passwords do not match")
        async def execute():
            try:
                async with get_session_factory()() as session:
                    if args.command == "admin-create":
                        await admin_create(session, email=args.email, password=password)
                        print("Administrator created")
                    else:
                        print(await invite_create(session, email=args.email, ttl_hours=args.ttl_hours))
            finally:
                await get_engine().dispose()
        try:
            asyncio.run(execute())
        except DomainError as exc:
            print(exc.code, file=sys.stderr)
            raise SystemExit(1) from None
    elif args.command == "scheduler":
        from webmonitor.workers.scheduler import run_scheduler
        asyncio.run(run_scheduler())
    elif args.command in {"http-worker", "browser-worker"}:
        from webmonitor.workers.collection import main as collect
        asyncio.run(collect(args.command.removesuffix("-worker")))
    elif args.command == "agent-worker":
        from webmonitor.workers.agent import run_agent_worker
        asyncio.run(run_agent_worker())
    elif args.command == "mailer":
        from webmonitor.workers.mailer import run_mailer
        asyncio.run(run_mailer())


if __name__ == "__main__":
    main()
