from __future__ import annotations

import argparse
import os
from pathlib import Path

from .inputs import load_batch
from .warehouse import build_warehouse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crewrift-event-warehouse")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build a policy-indexed event warehouse from a batch of episodes.")
    build.add_argument(
        "--input",
        "-i",
        action="append",
        required=True,
        type=Path,
        help="report_request.json file, or a directory scanned recursively for them. Repeatable.",
    )
    build.add_argument("--out", "-o", required=True, type=Path, help="Output dataset directory.")
    build.add_argument(
        "--workers",
        "-w",
        type=int,
        default=None,
        help="Parallel worker processes (default: CPU count).",
    )
    build.add_argument(
        "--snapshot-every",
        type=int,
        default=None,
        help="Replay snapshot cadence passed to expand_replay (default: 1).",
    )

    serve = sub.add_parser("serve", help="Serve a local HTML query dashboard over a built warehouse.")
    serve.add_argument("--out", "-o", required=True, type=Path, help="Warehouse dataset directory to query.")
    serve.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    serve.add_argument("--port", "-p", type=int, default=8765, help="Bind port (default: 8765).")

    suss = sub.add_parser(
        "suss",
        help="Extend a built warehouse with a chat_suss partition: label each meeting "
        "chat message with who it accuses (LLM, Bedrock Haiku). Needs AWS creds + boto3.",
    )
    suss.add_argument("--out", "-o", required=True, type=Path, help="Built warehouse dataset directory to extend.")
    suss.add_argument(
        "--refresh", action="store_true", help="Re-classify all chat texts, ignoring chat_suss_cache.json."
    )

    args = parser.parse_args(argv)
    if args.command == "build":
        return _run_build(args)
    if args.command == "serve":
        return _run_serve(args)
    if args.command == "suss":
        return _run_suss(args)
    parser.error(f"unknown command {args.command!r}")
    return 2


def _run_build(args: argparse.Namespace) -> int:
    if args.snapshot_every is not None:
        os.environ["CREWRIFT_EVENT_SNAPSHOT_EVERY"] = str(args.snapshot_every)

    episodes = load_batch(args.input)
    print(f"loaded {len(episodes)} unique episodes from {len(args.input)} input(s)")

    summary = build_warehouse(episodes, args.out, workers=args.workers)
    print(
        f"wrote {summary.out_dir}: "
        f"{summary.events_written} events across {summary.episodes_ok} episodes "
        f"({summary.episodes_cached} cached, {summary.episodes_skipped} skipped, "
        f"{summary.episodes_failed} failed), "
        f"{summary.distinct_policies} distinct policies"
    )
    return 0


def _run_serve(args: argparse.Namespace) -> int:
    from .dashboard import serve

    serve(args.out, host=args.host, port=args.port)
    return 0


def _run_suss(args: argparse.Namespace) -> int:
    from .suss import build_suss_partition

    build_suss_partition(args.out, refresh=args.refresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
