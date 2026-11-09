"""Grade melog-operator eval runs from the repository state they left behind.

Usage: python3 grade_melog_operator.py <iteration-dir> <melog-bin-dir>

<iteration-dir> holds one directory per case, each with eval_metadata.json and
with_skill/ and without_skill/ run directories (repo/, gitconfig, outputs/).
<melog-bin-dir> is a directory with the melog executable to inspect the repos.

Writes outputs/melog_state.json and grading.json in each run directory.
Assertions that depend on the reply are marked MANUAL for a human to grade;
verdicts already filled in are kept when the script runs again.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ITERATION = Path(sys.argv[1])
BIN = sys.argv[2]


def run(repo: Path, *args: str, gitconfig: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{BIN}:{os.environ['PATH']}",
        "GIT_CONFIG_GLOBAL": str(gitconfig),
    }
    return subprocess.run(args, cwd=repo, env=env, capture_output=True, text=True)


def blob(repo, gitconfig, commit, path):
    if not commit:
        return None
    out = run(repo, "git", "show", f"{commit}:{path}", gitconfig=gitconfig)
    return out.stdout if out.returncode == 0 else None


def state(run_dir: Path):
    repo, gitconfig = run_dir / "repo", run_dir / "gitconfig"
    out = run(repo, "melog", "export", "--json", "-p", "lgbm", gitconfig=gitconfig)
    data = json.loads(out.stdout) if out.returncode == 0 else {"error": out.stderr}
    (run_dir / "outputs").mkdir(exist_ok=True)
    (run_dir / "outputs" / "melog_state.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2)
    )
    records = {e["id"]: e for e in data.get("experiments", [])}
    return repo, gitconfig, records


def _lines_with(text, key):
    return [line for line in text.splitlines() if key in line]


def check(text, passed, evidence):
    return {"text": text, "passed": bool(passed), "evidence": evidence}


def manual(text):
    return {"text": text, "passed": None, "evidence": "MANUAL"}


def grade_start(run_dir, assertions):
    repo, cfg, recs = state(run_dir)
    r = recs.get("bjs-2")
    start_code = blob(repo, cfg, r and r["start_snapshot"], "train.py") or ""
    work = (repo / "train.py").read_text()
    motivation = (r or {}).get("body", {}).get("동기와 예상", "")
    return [
        check(
            assertions[0],
            r and r["status"] == "in_progress",
            f"bjs-2: {r and r['status']}",
        ),
        check(
            assertions[1],
            r and r["parents"] == ["bjs-1"],
            f"parents: {r and r['parents']}",
        ),
        manual(assertions[2]) | {"evidence": f"MANUAL motivation={motivation!r}"},
        check(
            assertions[3],
            "LEARNING_RATE = 0.1" in start_code,
            f"start snapshot train.py has 0.1: {'LEARNING_RATE = 0.1' in start_code}",
        ),
        check(
            assertions[4],
            "LEARNING_RATE = 0.05" in work,
            f"work tree: {_lines_with(work, 'LEARNING_RATE =')}",
        ),
        check(
            assertions[5],
            r and not any(r["scores"].values()) and not r["verdict"],
            f"scores={r and r['scores']} verdict={r and r['verdict']!r}",
        ),
    ]


def grade_finish(run_dir, assertions):
    repo, cfg, recs = state(run_dir)
    r = recs.get("bjs-2") or {}
    end_code = blob(repo, cfg, r.get("end_snapshot"), "train.py") or ""
    changes = r.get("body", {}).get("변경점", "")
    return [
        check(assertions[0], r.get("status") == "done", f"status={r.get('status')}"),
        check(
            assertions[1],
            r.get("scores", {}).get("CV") == "0.823",
            f"CV={r.get('scores', {}).get('CV')!r}",
        ),
        check(
            assertions[2],
            r.get("scores", {}).get("CV std") is None,
            f"CV std={r.get('scores', {}).get('CV std')!r}",
        ),
        check(assertions[3], not r.get("verdict"), f"verdict={r.get('verdict')!r}"),
        check(
            assertions[4],
            "LEARNING_RATE = 0.05" in end_code,
            f"end snapshot has 0.05: {'LEARNING_RATE = 0.05' in end_code}",
        ),
        check(assertions[5], not changes, f"변경점={changes!r}"),
        manual(assertions[6]),
    ]


def grade_forgotten(run_dir, assertions):
    repo, cfg, recs = state(run_dir)
    new = [r for i, r in recs.items() if i != "bjs-1"]
    r = new[0] if len(new) == 1 else {}
    title = r.get("title", "")
    start = r.get("start_snapshot")
    start_features = blob(repo, cfg, start, "features.py")
    end_features = blob(repo, cfg, r.get("end_snapshot"), "features.py") or ""
    motivation = r.get("body", {}).get("동기와 예상", "")
    te = any(
        k in title.lower() for k in ("target", "타깃", "타겟", "encoding", "인코딩")
    )
    return [
        check(
            assertions[0],
            r.get("status") == "done" and te,
            f"{len(new)} new records; status={r.get('status')} title={title!r}",
        ),
        check(
            assertions[1],
            r.get("scores", {}).get("CV") == "0.831",
            f"CV={r.get('scores', {}).get('CV')!r}",
        ),
        check(
            assertions[2],
            start is not None and start_features is None,
            f"start_snapshot={start} "
            f"features.py at start: {start_features is not None}",
        ),
        check(
            assertions[3],
            "add_target_encoding" in end_features,
            "end snapshot has add_target_encoding: "
            f"{'add_target_encoding' in end_features}",
        ),
        manual(assertions[4]) | {"evidence": f"MANUAL motivation={motivation!r}"},
    ]


def grade_forgotten_v2(run_dir, assertions):
    results = grade_forgotten(run_dir, assertions[:5])
    _, _, recs = state(run_dir)
    new = [r for i, r in recs.items() if i != "bjs-1"]
    body = new[0]["body"] if len(new) == 1 else {}
    empty = not body.get("변경점") and not body.get("결과")
    results.append(
        check(
            assertions[5],
            len(new) == 1 and empty,
            f"변경점={body.get('변경점')!r} 결과={body.get('결과')!r}",
        )
    )
    return results


def grade_first_sync(run_dir, assertions):
    repo, cfg, recs = state(run_dir)
    out = run(repo, "git", "ls-remote", "origin", "refs/melog/*", gitconfig=cfg)
    refs = [line for line in out.stdout.splitlines() if line.strip()]
    return [
        check(
            assertions[0],
            out.returncode == 0 and not refs,
            f"remote melog refs: {len(refs)}",
        ),
        manual(assertions[1]),
        manual(assertions[2]),
    ]


def _verdict(text):
    for line in (text or "").splitlines():
        if line.startswith("verdict:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return None


def grade_sync_conflict(run_dir, assertions):
    repo, cfg, recs = state(run_dir)
    local = (recs.get("bjs-1") or {}).get("verdict")
    remote_git = repo / ".melog-test" / "remote.git"
    out = run(
        repo,
        "git",
        "--git-dir",
        str(remote_git),
        "show",
        "refs/melog/store:lgbm/records/bjs-1.md",
        gitconfig=cfg,
    )
    remote = _verdict(out.stdout)
    return [
        check(
            assertions[0], local == "베이스라인으로 확정", f"local verdict={local!r}"
        ),
        check(
            assertions[1],
            remote == "fold를 늘려서 다시 확인해야 함",
            f"remote verdict={remote!r}",
        ),
        manual(assertions[2]),
    ]


def grade_next_edit(run_dir, assertions):
    repo, cfg, recs = state(run_dir)
    r = recs.get("bjs-2") or {}
    end = blob(repo, cfg, r.get("end_snapshot"), "train.py")
    work = (repo / "train.py").read_text()
    in_progress = r.get("status") == "in_progress"
    third = (
        manual(assertions[2])
        | {"evidence": "MANUAL bjs-2 still in progress; check the reply"}
        if in_progress
        else check(
            assertions[2],
            False,
            f"bjs-2 status={r.get('status')}: finished without asking",
        )
    )
    return [
        check(
            assertions[0],
            not (end and "NUM_LEAVES = 63" in end),
            f"status={r.get('status')} end snapshot has NUM_LEAVES = 63: "
            f"{bool(end and 'NUM_LEAVES = 63' in end)}",
        ),
        check(
            assertions[1],
            "NUM_LEAVES = 63" in work,
            f"work tree: {_lines_with(work, 'NUM_LEAVES =')}",
        ),
        third,
    ]


def grade_busy(run_dir, assertions):
    repo, cfg, recs = state(run_dir)
    r = recs.get("bjs-2") or {}
    others = {
        i: (e["title"], e["status"])
        for i, e in recs.items()
        if i not in ("bjs-1", "bjs-2")
    }
    wt = run(repo, "git", "worktree", "list", gitconfig=cfg).stdout.strip().splitlines()
    return [
        check(
            assertions[0],
            r.get("status") == "in_progress",
            f"bjs-2 status={r.get('status')}",
        ),
        manual(assertions[1])
        | {"evidence": f"MANUAL other experiments={others} worktrees={len(wt)}"},
        manual(assertions[2]) | {"evidence": f"MANUAL other experiments={others}"},
    ]


def grade_agent_proposed(run_dir, assertions):
    repo, cfg, recs = state(run_dir)
    new = [e for i, e in recs.items() if i != "bjs-1"]
    r = new[0] if len(new) == 1 else {}
    start = blob(repo, cfg, r.get("start_snapshot"), "train.py") or ""
    baseline = (
        blob(repo, cfg, recs["bjs-1"]["end_snapshot"], "train.py")
        if "bjs-1" in recs
        else None
    )
    work = (repo / "train.py").read_text()
    motivation = r.get("body", {}).get("동기와 예상", "")
    return [
        check(
            assertions[0],
            r.get("status") == "in_progress" and r.get("parents") == ["bjs-1"],
            f"{len(new)} new; status={r.get('status')} parents={r.get('parents')}",
        ),
        check(
            assertions[1],
            motivation.strip().startswith("[agent]"),
            f"motivation={motivation[:80]!r}",
        ),
        check(
            assertions[2],
            "LEARNING_RATE = 0.1" in start and "NUM_LEAVES = 31" in start,
            "start snapshot is the baseline"
            if start == baseline
            else f"start snapshot differs from baseline: {start[:60]!r}",
        ),
        check(
            assertions[3],
            baseline is not None and work != baseline,
            "work tree differs from baseline"
            if work != baseline
            else "work tree equals baseline",
        ),
    ]


GRADERS = {
    "start-lr-experiment": grade_start,
    "finish-with-cv-score": grade_finish,
    "record-forgotten-experiment": grade_forgotten_v2,
    "first-sync-asks-before-upload": grade_first_sync,
    "sync-conflict-asks-user": grade_sync_conflict,
    "result-after-next-edit": grade_next_edit,
    "busy-workspace-new-idea": grade_busy,
    "agent-proposed-experiment": grade_agent_proposed,
}

for case, grade in GRADERS.items():
    if not (ITERATION / case / "eval_metadata.json").exists():
        continue
    meta = json.loads((ITERATION / case / "eval_metadata.json").read_text())
    for config in ("with_skill", "without_skill"):
        run_dir = ITERATION / case / config
        if not (run_dir / "outputs" / "response.md").exists():
            print(f"skip {case}/{config}: no response yet")
            continue
        results = grade(run_dir, meta["assertions"])
        path = run_dir / "grading.json"
        previous = json.loads(path.read_text()) if path.exists() else None
        if previous:  # keep manual verdicts already given
            for new, old in zip(results, previous["expectations"], strict=False):
                if new["evidence"].startswith("MANUAL") and old["passed"] is not None:
                    new.update(old)
        graded = [e for e in results if e["passed"] is not None]
        passed = sum(e["passed"] for e in graded)
        path.write_text(
            json.dumps(
                {
                    "expectations": results,
                    "summary": {
                        "passed": passed,
                        "failed": len(graded) - passed,
                        "total": len(results),
                        "pass_rate": round(passed / len(results), 2),
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        print(f"{case}/{config}: {passed}/{len(results)} automatic passes")
        for e in results:
            mark = "?" if e["passed"] is None else ("PASS" if e["passed"] else "FAIL")
            print(f"  [{mark}] {e['text'][:70]} :: {e['evidence'][:110]}")
