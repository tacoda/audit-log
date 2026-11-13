"""audit-log: an append-only, tamper-evident record, as an iii worker.

**Why this is a worker.** A hash chain has exactly one writer or it has none. The
first real log this code produced failed its own verification with nothing having
tampered with it: two workers appended from separate processes, each held its own
in-memory tail, and their `prev` hashes interleaved. A thread lock cannot make
one writer true across processes. A worker makes it true by construction rather
than by discipline.

**Why it owns its storage.** Sequencing needs an authoritative tail, and
durability means an fsync before the caller is told the entry landed. Neither
survives being split from the store, so this worker is the store. What it can do
is tell everybody afterwards, which is what `audit::recorded` is for.

**The deciding is somebody else's job; the remembering is this one's.** A record
that lives inside the thing it records is not independent of it, so nothing here
decides anything. Callers append; this worker chains, fsyncs, and announces.

Every decision is made in `audit.py`, which is pure. `audit_log.py` is the I/O.
This file is the wiring.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from pathlib import Path

from iii import CallAction, InitOptions, register_worker

import audit
import audit_log
import schemas

HOME = Path(__file__).resolve().parents[1]

WORKER = None


def observer_added(obs) -> None:
    """One sibling now observes audit::recorded. Logged, nothing more."""
    print(f"  audit::recorded observed by {getattr(obs, 'target', '?')}")


def observer_removed(obs) -> None:
    print(f"  audit::recorded observation removed: {getattr(obs, 'target', '?')}")


def config() -> dict:
    """Where the log is, and what vocabulary it expects.

    `AUDIT_LOG_KINDS` is a comma-separated list of the event kinds this
    deployment uses. Unset means no vocabulary was declared and no kind is
    reported as unknown, which is the honest answer: nobody said what counts.
    """
    declared = os.environ.get("AUDIT_LOG_KINDS", "")
    return {
        "folder": Path(os.environ.get("AUDIT_LOG_DIR") or HOME / "audit"),
        "kinds": tuple(k.strip() for k in declared.split(",") if k.strip()),
    }


SETTINGS = config()
FOLDER = SETTINGS["folder"]
KINDS = SETTINGS["kinds"]
LOG = audit_log.AuditLog(FOLDER, kinds=KINDS)


async def announce(item: dict) -> None:
    """Tell whoever observes audit::recorded that an entry landed.

    One fire-and-forget call, after the fsync and off the critical path. The
    entry is already durable, so an observer that is down is an observer that
    missed something rather than a write that failed. The engine delivers to
    the observers, which is why one call is enough.

    Handlers run on the SDK event loop, so the call has to be the async one:
    a sync SDK method raises there instead of delivering.
    """
    if WORKER is None:
        return
    try:
        await WORKER.call_async({"function_id": "audit::recorded", "payload": item,
                                 "action": CallAction.Void()})
    except Exception as exc:  # noqa: BLE001
        print(f"audit::recorded call failed: {type(exc).__name__}: {exc}")


# ------------------------------------------------------------- the surface

def asked(payload: dict) -> dict:
    """The payload, whether it arrived wrapped or bare."""
    return payload.get("payload") or payload


def text(data: dict, key: str) -> str:
    """One string field, defaulting to empty rather than to `None`.

    Every field here is optional and every absent one means the same thing, so
    the coercion lives in one place instead of at each call site.
    """
    return str(data.get(key) or "")


async def fn_append(payload: dict) -> dict:
    """Record one decision. The only way anything gets into the log.

    Returns the entry's sequence number and hash, so a caller that wants to prove
    it recorded something has both to name.
    """
    data = asked(payload)
    kind = text(data, "kind")
    if not kind:
        return {"error": "an entry with no kind cannot be found later"}

    try:
        item = LOG.append(kind, actor=text(data, "actor"),
                          subject=text(data, "subject"),
                          detail=dict(data.get("detail") or {}))
    except Exception as exc:  # noqa: BLE001
        # The caller has already acted on the decision this was meant to record.
        # Failing loudly here is the only thing left that helps.
        print(f"AUDIT WRITE FAILED ({kind}): {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}"}

    await announce(item)
    # An unknown kind is a newer caller, not a bad one. A log that rejected
    # events it had not heard of would stop recording the day something new
    # happened, so this is reported rather than refused.
    return {"seq": item["seq"], "hash": item["hash"], "kind": kind,
            "known_kind": not KINDS or kind in KINDS}


async def fn_verify(payload: dict) -> dict:
    """Is the chain intact, and from where is it not.

    A verifier that answers only yes or no is useless in an audit, where the
    question is what exactly is being claimed and from when.
    """
    check = LOG.verify()
    return {
        "ok": check.ok,
        "entries": check.entries,
        "verified_through": check.verified_through,
        "problems": check.problems,
        "folder": str(FOLDER),
    }


async def fn_summary(payload: dict) -> dict:
    """Both halves from one file: whether it can be trusted, and what it counts."""
    data = asked(payload)
    fields = data.get("by") or ()
    if isinstance(fields, str):
        # The bus hands a list. The offline CLI hands `by=kind,actor` as one
        # string. One coercion, so the two answers cannot drift.
        fields = [f.strip() for f in fields.split(",") if f.strip()]
    fields = tuple(fields) or ("kind", "actor")
    return audit_log.summary(FOLDER, kinds=KINDS, breakdowns=fields)


async def fn_read(payload: dict) -> dict:
    """Entries, newest last. `kind` and `subject` narrow it."""
    data = asked(payload)
    entries, problems = LOG.read()

    for field in ("kind", "subject"):
        wanted = text(data, field)
        if wanted:
            entries = [e for e in entries if e.get(field) == wanted]

    try:
        limit = int(data.get("limit") or 200)
    except (TypeError, ValueError):
        return {"error": f"limit must be a number, got {data.get('limit')!r}"}
    return {"entries": entries[-limit:], "total": len(entries), "problems": problems}


async def fn_tally(payload: dict) -> dict:
    """Count entries by any field, narrowed to one kind.

    A deployment's interesting breakdowns are its own vocabulary. Counting
    `detail.rung` across every entry and counting it across refusals are
    different questions, and only the second has an answer worth reading.
    """
    data = asked(payload)
    entries, _ = LOG.read()
    return {"counts": audit.tally(entries, by=text(data, "by") or "kind",
                                  kind=text(data, "kind")),
            "total": len(entries)}


async def fn_head(payload: dict) -> dict:
    """The chain head: the one fact an anchor stores.

    `audit::recorded` hands every subscriber the head as it lands. This is the
    same fact for an anchor that was down, or for a third party that wants to
    know whether the log it anchored is still the log on disk.
    """
    return LOG.head()


async def fn_recorded(event: dict) -> dict:
    """The event source behind the announcement.

    An event source returns the event it is called with, and its observers
    observe that return value. Only this worker may call it, and it fires
    once per sealed entry, always after the fsync. An observer therefore
    never sees an entry that is not durable. Bind with
    `worker.observe(\"audit::recorded\", \"your::function\")`.
    """
    return event


# --------------------------------------------------------------- offline

# Reading and verifying a chain needs no engine, and the person most likely to
# want them is an auditor, who is the person least likely to have one running.
# Dispatched to the same handlers the bus calls, so the two answers cannot
# differ. Appending is deliberately absent: one writer is the whole guarantee,
# and a command line that appends beside a running worker is a second one.
OFFLINE = {
    "verify": fn_verify,
    "summary": fn_summary,
    "read": fn_read,
    "tally": fn_tally,
    "head": fn_head,
}


def summarise() -> int:
    """`make check`: whether the log can be trusted, and what it holds.

    Exits non-zero on a broken chain, so it works as a cron job or a CI step
    rather than only as something to read.
    """
    check = LOG.verify()
    counts = audit.tally(LOG.read()[0])
    point = LOG.head()
    print(f"{check.entries} entries in {FOLDER}")
    print(f"  chain    : {'intact' if check.ok else 'BROKEN'}, "
          f"verified through {check.verified_through}")
    print(f"  head     : seq {point['seq']} {point['hash'][:16]}…")
    print("  anchor this pair somewhere the log directory cannot reach. "
          "audit::recorded hands the head to observers on every entry")
    for kind, count in counts.items():
        print(f"  {count:6}  {kind}")
    for problem in check.problems:
        print(f"  PROBLEM: {problem}")
    return 0 if check.ok else 1


def cli(argv: list[str]) -> int:
    """`key=value` tokens, because that is how `iii trigger` takes a payload."""
    command = argv[0] if argv else "check"
    if command == "check":
        return summarise()

    handler = OFFLINE.get(command)
    if handler is None:
        print(f"no offline command {command!r}. Try: check, "
              f"{', '.join(sorted(OFFLINE))}")
        return 2

    answer = asyncio.run(
        handler(dict(t.split("=", 1) for t in argv[1:] if "=" in t)))
    print(json.dumps(answer, indent=2, default=str))
    return 1 if answer.get("error") else 0


def main() -> None:
    global WORKER
    url = os.environ.get("III_URL", "ws://localhost:49134")
    WORKER = register_worker(url, InitOptions(
        worker_name="audit-log",
        worker_description=("Append-only, hash-chained audit log of human "
                            "decisions. Callers append, this worker fsyncs, "
                            "chains and announces.")))

    # By id, and before the registration, so the hooks see every observer.
    WORKER.on_observer_added("audit::recorded", observer_added)
    WORKER.on_observer_removed("audit::recorded", observer_removed)

    for function_id, handler, description in (
        ("audit::append", fn_append,
         "Record one decision in the append-only, hash-chained log"),
        ("audit::verify", fn_verify,
         "Is the chain intact, and from which entry is it not"),
        ("audit::summary", fn_summary,
         "What the log says: whether it verifies, and what it counts"),
        ("audit::read", fn_read, "Entries, optionally narrowed by kind or subject"),
        ("audit::tally", fn_tally, "Count entries by any field, narrowed to one kind"),
        ("audit::head", fn_head,
         "The chain head, seq and hash. The fact an anchor stores"),
    ):
        spec = schemas.FUNCTIONS[function_id]
        WORKER.register_function(function_id, handler, description=description,
                                 request_format=spec["request"],
                                 response_format=spec["response"])

    # The event source is private: only this worker calls it, and only here.
    # Every other worker may observe it.
    WORKER.register_function(
        "audit::recorded", fn_recorded,
        description=("The sealed-entry event source. Observe it: your target "
                     "receives the entry, seq and hash included. Private, so "
                     "only this worker fires it"),
        request_format=schemas.ENTRY, response_format=schemas.ENTRY,
        private=True)

    check = LOG.verify()
    point = LOG.head()
    print(f"audit-log ready on {url}")
    print(f"  log      : {FOLDER}")
    print(f"  entries  : {check.entries} ({'intact' if check.ok else 'BROKEN'}), "
          f"head seq {point['seq']}")
    print(f"  kinds    : {', '.join(KINDS) if KINDS else 'undeclared, nothing checked'}")
    print("  announces: audit::recorded (private, observe it from any worker)")
    print("  one writer, by construction: this process owns the chain")
    for problem in check.problems[:5]:
        print(f"  PROBLEM: {problem}")
    threading.Event().wait()


if __name__ == "__main__":
    # No argument is the worker. Anything else is a question asked offline, so
    # `make run` and `make check` are the same file and cannot disagree.
    if sys.argv[1:]:
        raise SystemExit(cli(sys.argv[1:]))
    main()
