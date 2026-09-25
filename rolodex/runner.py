"""Start Claude Code from the app to do the analysis (the "Analyze now" button, and 3-month rechecks).

Claude Code runs headless in this folder (`claude -p`) and follows .claude/skills/analyze/SKILL.md,
reporting progress through `python -m rolodex.tasks`. It needs the `claude` command, signed in
once from a terminal (type `claude` and follow the sign-in link), or an ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading
from datetime import datetime, timedelta

from . import config, db

log = logging.getLogger("rolodex.runner")
_lock = threading.Lock()
_proc: subprocess.Popen | None = None

PROMPT = ("Run the rolodex analysis: follow .claude/skills/analyze/SKILL.md exactly, from start to finish, "
          "without asking questions.")

# What the unattended session may do without asking: the rolodex's own commands, web research,
# and files in work/. Nothing else.
ALLOWED_TOOLS = [
    "Bash(python -m rolodex.tasks:*)", "Bash(python3 -m rolodex.tasks:*)",
    "Bash(python -m rolodex.catalog:*)", "Bash(python3 -m rolodex.catalog:*)",
    "Bash(curl:*)", "Bash(mkdir:*)", "Bash(date:*)", "Bash(file:*)",
    "WebSearch", "WebFetch", "Read", "Edit(work/**)", "Glob", "Grep", "Agent", "Task",
]

LOG = lambda: config.CACHE_DIR / "analysis.log"


def available() -> str:
    """'' when Claude Code can be started, else why not."""
    if config.USE_API:
        return "The app does the analysis itself through the Claude API."
    if not shutil.which(config.CLAUDE_COMMAND):
        return "Claude Code isn't installed here."
    return ""


def running() -> bool:
    return _proc is not None and _proc.poll() is None


def waiting() -> tuple[list[dict], list[dict]]:
    """(cards/pamphlets to read, suppliers to research)."""
    cards = db.unread_cards()
    unread = {c["supplier_id"] for c in cards}
    return cards, [s for s in db.due_for_recheck() if s["id"] not in unread]


def start(trigger: str = "button") -> str:
    """Start a run; returns '' or why it didn't start."""
    global _proc
    with _lock:
        if running():
            return "An analysis is already running."
        why = available()
        if why:
            return why
        cards, research = waiting()
        if not cards and not research:
            return "Nothing is waiting to be analyzed."
        planned = list(dict.fromkeys([c["supplier_id"] for c in cards] + [s["id"] for s in research]))
        db.update_analysis(state="running", started_at=db.now(), finished_at=None, planned=planned, finished=[],
                           current="", summary="", trigger=trigger)
        cmd = [config.CLAUDE_COMMAND, "-p", PROMPT, "--output-format", "stream-json", "--verbose",
               "--allowedTools", *ALLOWED_TOOLS]
        env = {**os.environ, "ROLODEX_ANALYSIS": "1"}
        log_file = open(LOG(), "w", encoding="utf-8")
        try:
            _proc = subprocess.Popen(cmd, cwd=config.ROOT, stdout=log_file, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, env=env, start_new_session=True)
        except OSError as e:
            db.update_analysis(state="failed", finished_at=db.now(), summary=f"Couldn't start Claude Code: {e}")
            return f"Couldn't start Claude Code: {e}"
        db.update_analysis(pid=_proc.pid)
        threading.Thread(target=_watch, args=(_proc, log_file), daemon=True).start()
        return ""


def _watch(proc: subprocess.Popen, log_file) -> None:
    code = proc.wait()
    log_file.close()
    a = db.analysis()
    if a["state"] == "running":   # it didn't record the finish itself
        tail = _log_tail()
        if code == 0:
            db.update_analysis(state="done", finished_at=db.now(), current="", summary=a["summary"] or _summary(a))
        else:
            hint = (" Claude Code isn't signed in: open a terminal here, type claude and sign in, then try again."
                    if any(w in tail.lower() for w in ("login", "log in", "sign in", "api key", "authenticat")) else "")
            db.update_analysis(state="failed", finished_at=db.now(), current="",
                               summary=f"Claude Code stopped (exit {code}).{hint} Last output: {tail[-300:]}")
    _reset_leftovers()
    try:
        from . import gitsync
        gitsync.request()
    except Exception:
        pass


def _log_tail(limit: int = 12000) -> str:
    """Claude Code's output, readable: what it said and each tool it used (the log is stream-json)."""
    import json
    try:
        raw = LOG().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            lines.append(line)   # plain text (an error before the stream started)
            continue
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text" and block.get("text", "").strip():
                    lines.append(block["text"].strip())
                elif block.get("type") == "tool_use":
                    inp = block.get("input", {})
                    what = inp.get("command") or inp.get("query") or inp.get("url") or inp.get("file_path") or inp.get("description") or ""
                    lines.append(f"  [{block.get('name')}] {str(what)[:200]}")
        elif event.get("type") == "result":
            lines.append(f"== finished: {event.get('subtype', '')}" + (f", cost ${event['total_cost_usd']:.2f}" if event.get("total_cost_usd") else "")
                         + (f"\n{event.get('result')}" if event.get("is_error") else ""))
    return "\n".join(lines)[-limit:].strip()


def _summary(a: dict) -> str:
    done = [s for s in (db.get_supplier(i) for i in a["finished"]) if s and s["status"] != "error"]
    flagged = [s["company"] for s in done if s["needs_attention"]]
    return (f"Researched {len(done)} of {len(a['planned'])} supplier(s)."
            + (f" Needs attention: {', '.join(flagged)}." if flagged else ""))


def _reset_leftovers() -> None:
    """Suppliers left mid-research by a stopped run go back in the queue."""
    for s in db.all_suppliers():
        if s["status"] == "researching" or s["progress"]:
            db.update_supplier(s["id"], status="queued" if s["status"] == "researching" else s["status"], progress="")


def cancel() -> None:
    global _proc
    with _lock:
        if running():
            try:
                os.killpg(_proc.pid, signal.SIGTERM)
            except OSError:
                _proc.terminate()
        db.update_analysis(state="cancelled", finished_at=db.now(), current="",
                           summary="Cancelled. Anything not finished stays queued for the next run.")
    _reset_leftovers()


def progress() -> dict:
    """For the progress bar: state, done/total suppliers, the current one and its step."""
    a = db.analysis()
    if a["state"] == "running" and not running() and _proc is None:
        # The app restarted while a run was going: that run is gone.
        db.update_analysis(state="failed", finished_at=db.now(), summary="Stopped when the app restarted.")
        _reset_leftovers()
        a = db.analysis()
    planned = [db.get_supplier(i) for i in a["planned"]]
    planned = [s for s in planned if s]
    started = a.get("started_at") or ""
    done_ids = set(a["finished"])
    finished = [s for s in planned if s["id"] in done_ids]
    step = 0.0
    for s in planned:
        if s["progress"] and s not in finished:
            step += min(s["progress"].get("step", 0), s["progress"].get("of", 7)) / max(s["progress"].get("of", 7), 1)
    total = max(len(planned), 1)
    pct = round(100 * (len(finished) + step) / total) if a["state"] == "running" else 100
    return {"state": a["state"], "done": len(finished), "total": len(planned), "percent": min(pct, 99 if a["state"] == "running" else 100),
            "current": a["current"], "summary": a["summary"], "started_at": started, "finished_at": a.get("finished_at"),
            "steps": [{"company": s["company"], "label": s["progress"].get("label", ""), "step": s["progress"].get("step"),
                       "of": s["progress"].get("of")} for s in planned if s["progress"]]}


def start_rechecks(interval_seconds: int = 1800) -> None:
    """While the app is running, start a run when something is due (at most one try every 6 hours)."""
    def loop():
        import time
        last_try = datetime.min
        while True:
            time.sleep(interval_seconds)
            try:
                if not running() and datetime.now() - last_try > timedelta(hours=6) and any(waiting()):
                    last_try = datetime.now()
                    start("recheck")
            except Exception:
                log.exception("recheck loop error")
    threading.Thread(target=loop, name="rechecks", daemon=True).start()


def ask(question: str, directory_json: str, timeout: int = 240) -> dict:
    """Answer a plain-English question with Claude Code from the supplier directory.
    Returns {answer, matches: [{supplier_id, why}]}; raises RuntimeError with a readable reason."""
    import json
    import re
    prompt = (
        "You help a commercial bakery's purchasing staff pick suppliers. The supplier directory (JSON) is "
        "given on standard input. Answer the question below using only that directory. Recommend the "
        "suppliers that best fit, one sentence each on why, and mention anything that argues against one "
        "(a recall, a lapsed certification, out of area, needs attention). If nothing fits, say so plainly "
        "and suggest what kind of supplier to look for. Keep it short. Don't use any tools.\n\n"
        'Reply with only a JSON object: {"answer": "<your answer>", "matches": [{"supplier_id": <id>, '
        '"why": "<one sentence>"}]}\n\nQuestion: ' + question)
    try:
        r = subprocess.run([config.CLAUDE_COMMAND, "-p", prompt, "--output-format", "json"], input=directory_json,
                           capture_output=True, text=True, timeout=timeout, cwd=config.ROOT)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude took too long to answer. Try a shorter question.")
    out = r.stdout.strip()
    try:
        text = json.loads(out).get("result", "")
    except ValueError:
        text = out
    if r.returncode != 0 or not text:
        tail = (r.stdout + r.stderr)[-300:]
        hint = (" Claude Code isn't signed in: open a terminal, type claude and sign in."
                if any(w in tail.lower() for w in ("login", "log in", "api key", "authenticat")) else "")
        raise RuntimeError(f"Claude Code couldn't answer.{hint} ({tail.strip()})")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        result = json.loads(m.group(0)) if m else {"answer": text, "matches": []}
    except ValueError:
        result = {"answer": text, "matches": []}
    result.setdefault("matches", [])
    result["matches"] = [x for x in result["matches"] if isinstance(x, dict) and str(x.get("supplier_id", "")).isdigit()]
    for x in result["matches"]:
        x["supplier_id"] = int(x["supplier_id"])
    return {"answer": str(result.get("answer", "")), "matches": result["matches"]}
