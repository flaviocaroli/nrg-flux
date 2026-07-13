"""Manage customer API keys.

Usage (from backend/):
    python scripts/manage_clients.py create --name "Acme Trading" \\
        --email ops@acme.com --plan pro
    python scripts/manage_clients.py list
    python scripts/manage_clients.py revoke --prefix ab12cd34
    python scripts/manage_clients.py usage --prefix ab12cd34

Plans: free (10/min, 500/day) · pro (60/min, 10k/day) ·
       business (300/min, 100k/day) · enterprise (1200/min, 2M/day)

The plaintext key is printed ONCE at creation — store it in your password
manager and send it to the client over a secure channel. Only its hash is
kept in the database.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db.models import ApiClient, SessionLocal, init_db  # noqa: E402
from app.services.auth_service import (PLANS, create_client, revoke_client,  # noqa: E402
                                       usage_breakdown, usage_today)


def cmd_create(args):
    db = SessionLocal()
    try:
        client, key = create_client(db, name=args.name, email=args.email,
                                    plan=args.plan, notes=args.notes)
    except ValueError as e:
        sys.exit(str(e))
    limits = PLANS[client.plan]
    print("=" * 62)
    print(f"  Client  : {client.name}  (id {client.id})")
    print(f"  Plan    : {client.plan}  "
          f"({limits['per_minute']}/min, {limits['per_day']}/day)")
    print(f"  Prefix  : {client.key_prefix}   <- use this to revoke")
    print(f"  API KEY : {key}")
    print("=" * 62)
    print("  Shown ONCE. Store it securely; only a hash is saved.")
    db.close()


def cmd_list(args):
    db = SessionLocal()
    rows = db.execute(select(ApiClient).order_by(ApiClient.id)).scalars().all()
    if not rows:
        print("No clients yet. Create one with: manage_clients.py create --name ...")
        return
    print(f"{'id':>3}  {'prefix':<10} {'plan':<10} {'active':<7} "
          f"{'today':>6}  name")
    for r in rows:
        print(f"{r.id:>3}  {r.key_prefix:<10} {r.plan:<10} "
              f"{str(r.active):<7} {usage_today(db, r.id):>6}  {r.name}")
    db.close()


def cmd_revoke(args):
    db = SessionLocal()
    ok = revoke_client(db, args.prefix)
    print("Revoked." if ok else f"No client with prefix '{args.prefix}'.")
    db.close()


def cmd_usage(args):
    db = SessionLocal()
    row = db.execute(select(ApiClient).where(ApiClient.key_prefix == args.prefix)
                     ).scalar_one_or_none()
    if not row:
        sys.exit(f"No client with prefix '{args.prefix}'.")
    print(f"{row.name} ({row.plan}) — today: {usage_today(db, row.id)} calls")
    for u in usage_breakdown(db, row.id):
        print(f"  {u['count']:>6}  {u['endpoint']}")
    db.close()


def main():
    init_db()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="issue a new customer key")
    c.add_argument("--name", required=True)
    c.add_argument("--email", default="")
    c.add_argument("--plan", default="free",
                   choices=[p for p in PLANS if p != "internal"])
    c.add_argument("--notes", default="")
    c.set_defaults(func=cmd_create)

    l = sub.add_parser("list", help="list clients with today's usage")
    l.set_defaults(func=cmd_list)

    r = sub.add_parser("revoke", help="revoke a key by prefix")
    r.add_argument("--prefix", required=True)
    r.set_defaults(func=cmd_revoke)

    u = sub.add_parser("usage", help="today's usage for one client")
    u.add_argument("--prefix", required=True)
    u.set_defaults(func=cmd_usage)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
