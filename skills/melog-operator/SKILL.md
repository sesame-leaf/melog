---
name: melog-operator
description: Record ML experiments with the `melog` CLI so that every attempt leaves a structured record and a code snapshot for the final report. Use whenever the user, in a git repository, starts trying an idea, changes a model or features for a Kaggle/DACON/competition run, finishes a run or reports CV/LB scores, says they forgot to record something, wants to fill in or backfill a record, compare or reproduce an earlier experiment's code, import an experiment from another model's project, share records with teammates, or look back at the lineage or collect records for a write-up. Use it proactively even when the user never says "melog" or "record", as long as melog is installed or the repo has melog projects.
---

# Skill: melog Experiment Recorder

Operate the `melog` CLI on the user's behalf so that every ML experiment is recorded: what was tried and why, which code produced which scores, and how experiments build on each other. The records become the raw material for the report the user writes after a competition.

## Installation

If `melog --help` fails, melog can be installed with `uv tool install git+https://github.com/sesame-leaf/melog`. It needs git and must run inside a git repository.

## When to activate

- The user is about to try an idea ("let's try target encoding", "lower the learning rate").
- A run finished, a notebook came back from Kaggle/Colab, or the user reports CV or leaderboard scores.
- The user wants to see, fix, or fill in past records, compare code between experiments, return to an old experiment's code, share records, or gather material for a report.

To check whether a repository uses melog, run `melog list --json`. An error saying no project exists means melog is not set up here yet; see "Setting up" before creating anything.

## Mental model (read this first)

- **An experiment is one attempt, sized by the user.** Changing one hyperparameter and rebuilding the whole pipeline are both experiments. Its life is: `start` (status `in_progress`) → `done` or `abandoned`. Both end states are final; trying again means a new experiment with the old one as a parent.
- **Snapshots capture the whole work tree.** `start` and `done`/`abandon` each commit the current files, including uncommitted and untracked ones that git does not ignore, to melog's own refs (`refs/melog/...`). The user's branches, index, and history are never touched, and the user commits whenever they like. The end snapshot *is* the experiment's code, so finish an experiment right after the run whose result you record, before anyone edits code for the next idea.
- **A record has facts and content.** Facts are written by melog and cannot be changed: id, times, snapshots, status, origin. Content can be edited at any time: title, parents, entry points, scores, verdict, and four body sections (`동기와 예상`, `변경점`, `결과`, `메모`, which mean motivation and expectation, changes, results, notes).
- **One experiment in progress per workspace.** Two experiments in one work tree would mix their snapshots. Parallel work needs separate workspaces (git worktrees, e.g. from `melog checkout`), unless the project was created with `--horizontal` because every experiment lives in its own files.
- **A melog project is an isolated set of experiments**, often one per model (`lgbm`, `bert`). Parents must be in the same project; experiments from another project come in with `melog import`.
- **melog records; it never judges.** It does not rank experiments or pick a best one.

## Who writes what

The records are only useful if they reflect what was actually thought at the time, so be careful about authorship.

| Part of the record | Who provides it |
|---|---|
| Motivation (`동기와 예상`) | The user. Ask for one line (what, why, what they expect) unless they already said it in the conversation; then use their words. If **you** designed or proposed the experiment, write your own reasoning and start it with `[agent]` so the report can tell whose idea it was. Never reconstruct a motivation after seeing the results. If the user declines, leave it empty. |
| Scores | The run's output or the user's report. Copy numbers exactly as printed; melog preserves notation such as `0.80`. |
| Verdict | The user's judgment. If you made the call (for example, you decided to drop an approach), write it with an `[agent]` prefix. |
| Changes (`변경점`) and results (`결과`) | You draft them from `melog diff` and the run's output, **show the draft to the user, and record it after they confirm or correct it.** |
| Notes (`메모`) | The user's interpretation and next ideas. Your own notes are fine with an `[agent]` prefix. |

Write record content in the user's language.

**Keep the cost of recording low.** Records only keep coming if each one costs the user little. Ask only for what you cannot get anywhere else: the motivation when it is missing, and approval of the drafts you wrote. Everything else (verdict, notes, CV std, leaderboard scores) can be added at any time with `melog edit`, so mention once that the user can tell you later instead of asking for each item.

## Calling melog as an agent

- **Always pass `--json`.** melog then never prompts and prints one JSON document to stdout. Exit code 0 is success, 1 is an error melog explains on stderr, 2 is a usage error.
- **Pass every value as an option.** Multi-line sections go through stdin, one section per call:
  ```bash
  melog edit bjs-3 --changes - --json <<'EOF'
  - num_leaves 31 -> 63
  - learning_rate 0.1 -> 0.05
  EOF
  ```
- **Never open the editor modes.** `melog edit` and `melog project edit` without options open an editor and need a human at a terminal. Use the options instead.
- **Ids can usually be omitted.** `done`, `abandon`, and `edit` default to the experiment in progress in this workspace. With several melog projects, pass `-p <project>`.
- **Initials.** Experiment ids look like `bjs-12`, from the git config `melog.initials`. If melog reports that initials are not set, ask the user which initials to use and run `git config --global melog.initials <initials>` with their agreement.
- Use `melog <command> --help` for every option.

## Workflow

### Setting up (once per repository or model)

1. Ask before creating a project, and agree on a name (lowercase letters, digits, `-`, `_`): `melog init lgbm --json`. Default scores are `CV`, `CV std`, `public LB`; offer `--scores` when the competition uses other names, such as `--scores "AUC,AUC std,public LB"`.
2. Use `--horizontal` only when every experiment lives in its own files and no shared code is edited in place.
3. Check `.gitignore` for data, model weights, submission files, and secrets such as `.env`. Snapshots include every file git does not ignore, and `melog sync` uploads them. Propose additions to the user rather than editing silently.

### Starting an experiment

1. Get the motivation (see "Who writes what") and the parents: which earlier experiment this builds on. Look at `melog list --json` or `melog lineage --json` if unsure, and ask when it is ambiguous.
2. Start **before** the code changes, so the start snapshot is the baseline for the diff:
   ```bash
   melog start "lower learning rate" --parent bjs-2 --motivation "..." --entry train.py --json
   ```
   `--entry` names the file(s) that represent the experiment, such as the notebook or training script.
3. If `large_untracked_files` in the response is not empty, tell the user which files were captured; they probably belong in `.gitignore`.
4. If melog says the workspace already has an experiment in progress, ask the user whether to finish or abandon it first, or to work in another worktree (`melog checkout <id>` creates one that starts from an experiment's code).

### Finishing an experiment

1. Notebooks: ask the user to save the notebook first. The snapshot reads the file on disk, not the editor's unsaved state.
2. Make sure the end snapshot will hold the code that produced the result, because it can never be changed afterwards. If the code may have changed since the run (the user says they already started on the next idea, or `melog diff` shows edits unrelated to this experiment), do not finish yet. Tell the user what would be captured and ask how to separate it, for example by undoing the unrelated edit until after `done`. Never discard, stash, or revert their work on your own.
3. Finish as soon as the result exists, even before the record is filled in:
   ```bash
   melog done --score CV=0.812 --score "CV std=0.004" --json
   ```
   Use the project's score names exactly (they are case-sensitive and listed under `project.scores` in `melog export --json`). Other names are allowed for one-off measurements.
4. If the attempt was dropped, run `melog abandon --verdict "<reason>" --json`. Abandoned experiments are kept and can still be parents.
5. If the user ran an experiment without starting it, record it afterwards with `melog done --new "title" --since <commit> --json`, where `<commit>` is where the work began (omit `--since` if unknown). The end snapshot is the current work tree, so if the code may differ from what ran, say what you assumed. melog refuses this while another experiment is in progress in the workspace, unless every experiment in progress belongs to a horizontal project.

### Filling in the record

1. Run `melog diff <id> --json` (or `--stat` for an overview). Notebooks are compared by code cells only. In horizontal projects, compare the experiment's notebook with its parent's: `melog diff <id> --against <parent> --entry-points --json`.
2. Summarize what changed in plain sentences, grouping related lines, rather than pasting the diff. Draft the results from the run's output.
3. Show the drafts, then record what the user approves with `melog edit <id> --changes - --json` and `--result - --json`.

### Updating records later

- Leaderboard scores arrive after submission: `melog edit bjs-3 --score "public LB=0.805" --json`. After the competition, add `private LB` the same way; it does not need to be in the project's score list.
- `melog edit` changes only the fields you pass, so concurrent edits to other fields elsewhere are kept.

### Reproducing and reusing

- `melog checkout <id> [path] --json` puts an experiment's end snapshot into a new git worktree next to the repository. Files git ignores, such as data, are not included. The worktree is a workspace of its own: start a new experiment there with `--parent <id>`.
- `melog import <project>/<id> -p <this-project> --json` brings an experiment from another model's project, for example for an ensemble. Scores are not copied, because the two projects may validate differently.

### Sharing with teammates

- Run `melog sync --json` when the user asks to share or agrees to it; never on your own.
- The first sync with a remote fails without `--yes`, because snapshots may contain files that were never committed. Show the user that this uploads code snapshots, ask about sensitive untracked files, and pass `--yes` only after they agree.
- On conflicts, melog changes nothing and lists them. Show the user each conflict and let them decide. `--prefer local` or `--prefer remote` settles **all** conflicts one way, so pass it only when the user chose that.
- If the report shows `renamed`, tell the user: a local experiment got a new id because another clone used the same one.

### Looking back

- `melog list --json`, `melog show <id> --json`, `melog lineage` (tree) or `melog lineage --mermaid`.
- `melog export -o experiments.md` collects the overview, lineage, and every record into one Markdown document for the report; add `--with-diffs` for changed files, or use `--json` to work with the data yourself.

## Safety

- Never edit `refs/melog/*` or melog's files with git plumbing; use melog commands.
- `--force` on `done`/`abandon` finishes an experiment started in another workspace, but the end snapshot then captures **this** workspace's code. Use it only when the original workspace is gone and the user agrees.
- Do not remove a worktree (`git worktree remove`) while an experiment is in progress in it.

## Recovery

| melog says | Do this |
|---|---|
| `this workspace already has an experiment in progress` | Finish or abandon it, or use another worktree. Ask the user which. |
| `was started in another workspace` | Run the command in that workspace. `--force` only if it no longer exists. |
| `no experiment is in progress in this workspace` | Pass the id, or use `done --new` for an experiment that was never started. |
| `several melog projects exist` | Pass `-p <project>`. |
| `your initials for experiment ids are not set` | Ask the user and set `melog.initials`. |
| `parent experiments not found` | Check ids with `melog list --json`; parents must be in the same project, otherwise `melog import` first. |
| `local and remote changed the same things differently` | Show the conflicts and ask the user which side to keep. |
| `kept changing while syncing` | Someone else is syncing; run `melog sync --json` again. |
