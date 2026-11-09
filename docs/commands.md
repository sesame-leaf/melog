# melog 명령어 명세

melog의 모든 명령의 인자, 옵션, 동작, 오류, 출력 형식을 정리한 문서입니다. 각 명령을 왜 이렇게 설계했는지는 [design.md](design.md)에서 다룹니다. 이 문서에서 쓰는 용어는 [README](../README.md)의 정의를 따릅니다.

## 공통 규칙

### 공통 옵션

| 옵션 | 적용 대상 | 설명 |
|---|---|---|
| `--project`, `-p NAME` | `init`과 `sync`를 뺀 모든 명령 | 작업할 melog 프로젝트를 지정합니다 |
| `--json` | 모든 명령 | 기계가 읽을 수 있는 JSON을 표준 출력으로 내보냅니다. 이 옵션을 주면 아무것도 묻지 않습니다 |
| `--help` | 모든 명령 | 명령의 도움말을 보여 줍니다 |

### 프로젝트를 정하는 규칙

`--project`를 생략하면 다음 순서로 프로젝트를 정합니다.

1. 저장소에 melog 프로젝트가 하나뿐이면 그 프로젝트를 씁니다.
2. 이 작업 공간에서 진행 중인 실험이 모두 한 프로젝트에 속하면 그 프로젝트를 씁니다.
3. 둘 다 해당하지 않으면 프로젝트 목록을 보여 주고 오류로 종료합니다.

### 대상 실험을 정하는 규칙

`done`, `abandon`, `edit`은 ID를 생략할 수 있습니다. 생략하면 이 작업 공간에서 진행 중인 실험을 대상으로 삼습니다.

- 진행 중인 실험이 없으면 오류로 종료합니다.
- 진행 중인 실험이 여러 개면(수평 구조 프로젝트) 목록을 보여 주고 오류로 종료합니다.

### 대화형 입력

표준 입력이 터미널이고 `--json`을 주지 않았을 때만, 옵션으로 주지 않은 값을 묻습니다. 질문하기 전에 명령을 실행할 수 있는지 먼저 검사하므로, 입력한 내용이 헛수고가 되지 않습니다.

| 명령 | 묻는 값 | 건너뛸 수 있는가 |
|---|---|---|
| `start` | 제목(인자로 주지 않은 경우), 동기 | 제목은 필수이고, 동기는 Enter로 건너뜁니다 |
| `done` | 점수 구성의 점수 중 `--score`로 주지 않은 것, 판정 | 모두 Enter로 건너뜁니다 |
| `done --new` | 위의 값에 더해 동기 | 모두 Enter로 건너뜁니다 |
| `import` | 동기 | Enter로 건너뜁니다 |
| `start`, `done --new`, `import` | 이니셜(설정되어 있지 않은 경우) | 필수입니다. 입력한 값은 전역 git 설정에 저장합니다 |

터미널이 아닐 때 필수 값이 빠져 있으면 오류로 종료합니다.

### 출력과 종료 코드

- 사람이 읽는 출력은 표준 출력으로, 오류(`error: ...`)와 경고(`warning: ...`)는 표준 오류로 내보냅니다.
- 메시지는 영어로 출력합니다. 실험 기록 본문의 절 제목과 `export` 문서는 한국어로 씁니다.

| 종료 코드 | 뜻 |
|---|---|
| `0` | 성공. 에디터에서 아무것도 바꾸지 않았거나 편집을 취소한 경우도 포함합니다 |
| `1` | melog가 처리한 오류. 검증 실패, 충돌, 작업 공간 규칙 위반, 사용자의 취소 등입니다 |
| `2` | 사용법 오류. 모르는 옵션이나 빠진 필수 인자처럼, 명령줄을 해석하는 단계에서 생긴 오류입니다 |

### 실험 객체 (JSON)

실험 기록을 JSON으로 출력하는 명령은 모두 같은 형태의 실험 객체를 씁니다.

| 키 | 형식 | 설명 |
|---|---|---|
| `project` | 문자열 | melog 프로젝트 이름 |
| `id` | 문자열 | 실험 id (예: `bjs-3`) |
| `title` | 문자열 | 제목 |
| `status` | 문자열 | `in_progress`, `done`, `abandoned` 중 하나 |
| `started_at`, `ended_at` | 문자열 또는 `null` | 시간대를 포함한 ISO 8601 시각 |
| `start_snapshot`, `end_snapshot` | 문자열 또는 `null` | 스냅샷 커밋의 해시 |
| `origin` | 객체 또는 `null` | 가져온 실험의 출처. `project`, `id`, `snapshot` 키를 가집니다 |
| `parents` | 문자열 배열 | 부모 실험의 id |
| `entry_points` | 문자열 배열 | 진입점 경로 |
| `scores` | 객체 | 점수 이름을 키로, 적힌 그대로의 값(문자열) 또는 `null`을 값으로 가집니다 |
| `verdict` | 문자열 또는 `null` | 판정 |
| `body` | 객체 | `동기와 예상`, `변경점`, `결과`, `메모` 네 키를 가지며, 값은 각 절의 본문입니다 |

---

## 프로젝트 명령

### `melog init NAME`

melog 프로젝트를 만듭니다.

| 인자, 옵션 | 설명 |
|---|---|
| `NAME` | 프로젝트 이름. 영문 소문자, 숫자, `-`, `_`만 쓸 수 있고, 영문 소문자나 숫자로 시작해야 합니다 |
| `--scores TEXT` | 기본으로 기록할 점수 이름을 쉼표로 구분해서 줍니다. 생략하면 `CV`, `CV std`, `public LB`입니다. 점수 이름에는 쉼표를 쓸 수 없습니다 |
| `--horizontal` | 수평 구조 프로젝트로 만듭니다. 하나의 작업 공간에서 여러 실험을 동시에 진행할 수 있게 됩니다. 실험마다 파일이 분리되는 순수한 수평 구조에서만 씁니다 |

- **오류**: git 저장소 밖에서 실행함, 이름 규칙 위반, 이미 있는 이름, 점수 이름이 비어 있거나 겹침
- **출력**:
  ```
  Created melog project 'lgbm'.
    scores:     CV, CV std, public LB
    horizontal: no
  ```
- **JSON**: `{"project": {"name": ..., "scores": [...], "horizontal": ...}}`

### `melog project edit`

melog 프로젝트의 설정을 바꾸거나 개요를 편집합니다.

| 옵션 | 설명 |
|---|---|
| `--scores TEXT` | 점수 구성 전체를 주어진 목록으로 바꿉니다. 이미 남긴 실험 기록은 바뀌지 않습니다 |
| `--horizontal` / `--no-horizontal` | 수평 구조 여부를 바꿉니다 |

- 옵션을 하나라도 주면 설정만 바꾸고, 출력과 JSON은 `init`과 같습니다.
- 옵션 없이 터미널에서 실행하면 개요를 에디터로 엽니다. 개요는 자유 서술이므로 검증하지 않습니다. 바꾸지 않고 닫으면 `No changes.`를 출력합니다.
- **오류**: 터미널이 아니거나 `--json`을 준 상태에서 옵션 없이 실행함, 에디터를 열어 둔 동안 다른 곳에서 개요를 고침(고친 내용은 임시 파일로 남기고 경로를 알려 줍니다)

---

## 기록 명령

### `melog start [TITLE]`

실험을 시작하고, 지금의 코드를 시작 스냅샷으로 남깁니다.

| 인자, 옵션 | 설명 |
|---|---|
| `TITLE` | 실험의 제목. 생략하면 터미널에서 묻습니다 |
| `--parent ID` | 부모로 선언할 실험. 여러 번 줄 수 있습니다 |
| `--motivation TEXT` | "동기와 예상" 절에 들어갈 내용 |
| `--entry PATH` | 진입점. 여러 번 줄 수 있습니다 |

- **동작**: id와 시작 시각을 발급하고, 시작 스냅샷을 남기고, 상태가 `in_progress`인 실험 기록을 만들고, 이 작업 공간의 진행 중인 실험으로 등록합니다. 점수 구성의 점수는 빈 값으로 미리 채워 둡니다.
- **오류**: 제목이 비어 있음, 부모가 이 프로젝트에 없음, 작업 공간 규칙 위반(이 작업 공간에 이미 진행 중인 실험이 있음), 터미널이 아닌데 이니셜이 설정되어 있지 않음
- **경고**: git이 추적하지 않는 10MB 이상의 파일이 스냅샷에 들어가면 파일 목록과 함께 경고합니다.
- **출력**:
  ```
  Started bjs-3: LightGBM baseline (lgbm)
    parents: bjs-1
  Run 'melog done' when the experiment is finished.
  ```
- **JSON**: `{"experiment": <실험 객체>, "large_untracked_files": [{"path": ..., "bytes": ...}]}`

### `melog done [ID]`

실험을 끝내고, 지금의 코드를 종료 스냅샷으로 남깁니다.

| 인자, 옵션 | 설명 |
|---|---|
| `ID` | 끝낼 실험. 생략하면 이 작업 공간에서 진행 중인 실험입니다 |
| `--score NAME=VALUE` | 점수. 여러 번 줄 수 있고, 점수 구성에 없는 이름도 쓸 수 있습니다. 값은 숫자여야 하며, 적은 표기(예: `0.80`)를 그대로 보존합니다 |
| `--verdict TEXT` | 판정 |
| `--force` | 다른 작업 공간에서 시작한 실험을 이 작업 공간에서 끝냅니다. 종료 스냅샷은 이 작업 공간의 코드를 담습니다 |
| `--new TITLE` | 사후 등록. 시작을 선언하지 않고 진행한 실험을 시작과 완료를 한꺼번에 기록합니다 |
| `--since REV` | `--new`와 함께 씁니다. 시작 스냅샷으로 삼을 git 리비전입니다. 시작 시각은 그 커밋의 시각이 됩니다 |
| `--parent ID`, `--motivation TEXT`, `--entry PATH` | `--new`와 함께 씁니다. `start`의 같은 옵션과 같습니다 |

- **동작**: 종료 시각과 종료 스냅샷을 남기고, 상태를 `done`으로 바꾸고, 이 작업 공간의 진행 중인 실험에서 뺍니다. `--new`를 주면 새 실험 기록을 바로 `done` 상태로 만듭니다.
- **오류**: 대상 실험이 없음, 대상이 진행 중이 아님, 다른 작업 공간에서 시작한 실험인데 `--force`가 없음, 점수가 숫자가 아님, `--new`와 ID를 함께 줌, `--new` 없이 `--since`/`--parent`/`--motivation`/`--entry`를 줌, `--new`와 `--force`를 함께 줌, 수평 구조가 아닌 프로젝트에서 진행 중인 실험이 있는 작업 공간에서 `--new`를 실행함
- **출력**:
  ```
  Finished bjs-3: LightGBM baseline (lgbm)
    CV: 0.812  CV std: 0.004  public LB: -
    verdict: good baseline
  Fill in the record later with 'melog edit bjs-3'.
  ```
  `--new`를 주면 첫 줄이 `Recorded`로 시작합니다.
- **JSON**: `start`와 같습니다.

### `melog abandon [ID]`

실험을 중단합니다. 중단한 실험도 실험 기록으로 남고, 나중에 부모로 삼을 수 있습니다.

| 인자, 옵션 | 설명 |
|---|---|
| `ID` | 중단할 실험. 생략하면 이 작업 공간에서 진행 중인 실험입니다 |
| `--verdict TEXT` | 판정(예: 중단한 이유) |
| `--force` | `done`의 같은 옵션과 같습니다 |

- **동작**: 종료 시각과 종료 스냅샷을 남기고 상태를 `abandoned`로 바꿉니다. 아무것도 묻지 않습니다.
- **오류**: `done`과 같습니다(`--new` 관련 오류 제외).
- **출력**: 첫 줄이 `Abandoned`로 시작하고, 마지막 줄에서 `--parent ID`로 새 실험을 시작할 수 있다고 안내합니다.
- **JSON**: `start`와 같습니다.

### `melog import PROJECT/ID`

다른 melog 프로젝트의 실험을 이 프로젝트의 새 실험으로 가져옵니다.

| 인자, 옵션 | 설명 |
|---|---|
| `PROJECT/ID` | 가져올 실험(예: `lgbm/bjs-3`) |
| `--title TEXT` | 새 제목. 생략하면 원래 실험의 제목을 씁니다 |
| `--motivation TEXT` | 가져온 이유. "동기와 예상" 절에 들어갑니다 |

- **동작**: 이 프로젝트에서 새 id를 발급하고, 상태가 `done`인 실험 기록을 만듭니다. 종료 스냅샷은 원래 실험의 종료 스냅샷이고, 진입점은 원래 실험의 것을 씁니다. 출처를 기록하며, 점수, 판정, 본문은 복사하지 않습니다. 현재 작업 트리의 스냅샷을 남기지 않으므로 작업 공간 규칙을 검사하지 않습니다.
- **오류**: `PROJECT/ID` 형식이 아님, 같은 프로젝트의 실험임(`--parent`를 쓰라고 안내), 원래 실험이 없거나 진행 중임, 이미 가져온 실험임(이미 가져온 실험의 id를 알려 줌)
- **출력**:
  ```
  Imported lgbm/bjs-3 into ens as bjs-1: LightGBM baseline
  Use it as a parent with 'melog start --parent bjs-1'.
  ```
- **JSON**: `{"experiment": <실험 객체>}`

### `melog edit [ID]`

실험 기록을 고칩니다. 옵션을 하나라도 주면 에디터 없이 그 항목만 고치고, 옵션이 없으면 에디터를 엽니다.

| 인자, 옵션 | 설명 |
|---|---|
| `ID` | 고칠 실험. 생략하면 이 작업 공간에서 진행 중인 실험입니다 |
| `--title TEXT` | 제목을 바꿉니다 |
| `--parent ID` | 부모 목록 전체를 주어진 목록으로 바꿉니다. 여러 번 줄 수 있습니다 |
| `--clear-parents` | 부모를 모두 지웁니다 |
| `--entry PATH` | 진입점 목록 전체를 주어진 목록으로 바꿉니다. 여러 번 줄 수 있습니다 |
| `--clear-entries` | 진입점을 모두 지웁니다 |
| `--score NAME=VALUE` | 점수를 정합니다. `NAME=`처럼 값을 비우면 빈 값이 됩니다. 여러 번 줄 수 있습니다 |
| `--verdict TEXT` | 판정을 바꿉니다. 빈 문자열을 주면 지웁니다 |
| `--motivation`, `--changes`, `--result`, `--memo TEXT` | 본문의 각 절(동기와 예상, 변경점, 결과, 메모)을 바꿉니다. 값으로 `-`를 주면 표준 입력에서 읽습니다. `-`는 한 번에 한 옵션에만 쓸 수 있습니다 |

**에디터 방식**

- git이 쓰는 에디터(`GIT_EDITOR`, `core.editor`, `VISUAL`, `EDITOR`의 순서)로 편집용 문서를 엽니다. melog가 남긴 사실(상태, 시각, 출처)은 frontmatter 맨 위에 주석으로만 보여 줍니다.
- 바꾸지 않고 닫으면 `No changes.`, 내용을 전부 지우고 저장하면 `Edit cancelled.`를 출력합니다.
- 검증에 실패하면 오류를 보여 주고 `Edit again?`이라고 묻습니다. 다시 편집하지 않으면, 고친 내용을 임시 파일로 남기고 그 경로를 알려 준 뒤 종료 코드 1로 끝납니다.

**검증과 병합**

- 제목은 비어 있으면 안 됩니다. 부모는 같은 프로젝트에 있어야 하고, 자기 자신이면 안 되며, 부모 관계에 순환이 생기면 안 됩니다. 점수는 숫자이거나 비어 있어야 합니다.
- 에디터 방식에서는 frontmatter에 모르는 항목이나 고칠 수 없는 항목이 있으면 오류이고, 본문의 네 절 제목이 각각 정확히 한 번씩 있어야 합니다.
- 사용자가 고친 항목만 반영합니다. 편집하는 동안 다른 곳에서 같은 기록의 다른 항목을 고쳤다면 그 변경은 그대로 남습니다. 같은 항목을 서로 다르게 고쳤다면 오류로 종료하고, 에디터 방식에서는 고친 내용을 임시 파일로 남깁니다.

- **오류**: 위의 검증 실패, 대상 실험이 없음, `--parent`와 `--clear-parents`(또는 `--entry`와 `--clear-entries`)를 함께 줌, `-`를 두 옵션 이상에 줌, 터미널이 아니거나 `--json`을 준 상태에서 옵션 없이 실행함
- **출력**: `Updated bjs-3: verdict, score 'public LB', 변경점` 또는 `No changes.`
- **JSON**: `{"experiment": <실험 객체>, "changed": ["verdict", "scores.public LB", "body.변경점"]}`. 에디터 방식은 JSON을 지원하지 않습니다.

---

## 조회 명령

### `melog show ID`

실험 기록 하나를 보여 줍니다.

- **출력**: 첫 줄에 `ID: 제목`, 그 아래에 프로젝트, 상태, 시작, 종료, 부모, 출처(가져온 실험만), 진입점, 점수, 판정을 보여 주고, 마지막에 본문 네 절을 보여 줍니다. 시각은 사용자의 시간대로 보여 줍니다.
- **오류**: 실험이 없음
- **JSON**: `{"experiment": <실험 객체>}`

### `melog list`

프로젝트의 실험 목록을 보여 줍니다.

| 옵션 | 설명 |
|---|---|
| `--status STATUS` | 이 상태의 실험만 보여 줍니다. `in_progress`, `done`, `abandoned` 중 하나입니다 |

- **정렬**: 시작 시각 순서이고, 시작 시각이 없으면 종료 시각을 씁니다. 시각이 같으면 id 순서이며, 순번은 숫자로 비교합니다(`bjs-2`가 `bjs-10`보다 앞).
- **출력**: 한 줄에 실험 하나씩 id, 상태, 제목, 점수 구성의 점수를 보여 줍니다. 이 작업 공간에서 진행 중인 실험에는 `*`를 붙입니다.
  ```
     ID     STATUS       TITLE              CV     CV std  public LB
     bjs-1  done         LightGBM baseline  0.812  0.004   0.805
  *  bjs-2  in_progress  lower lr           -      -       -
  ```
- **JSON**: `{"project": ..., "experiments": [<실험 객체>, ...]}`. 각 실험 객체에는 `in_progress_here`(이 작업 공간에서 진행 중인지) 키가 더해집니다.

### `melog lineage`

실험의 계보를 보여 줍니다. 실험 사이의 우열은 표시하지 않습니다.

| 옵션 | 설명 |
|---|---|
| `--mermaid` | 트리 대신 mermaid 그래프 코드를 출력합니다 |
| `--score NAME` | 노드에 보여 줄 점수. 생략하면 점수 구성의 첫 번째 점수입니다 |

- **트리 형식**: 부모가 여럿인 실험은 첫 번째로 선언된 부모 아래에 놓고, 나머지 부모는 `(also from ...)`로 적습니다. 가져온 실험은 `(imported from PROJECT/ID)`를 붙입니다.
  ```
  bjs-1     done       LightGBM baseline  CV 0.812
  ├─ bjs-2  abandoned  lower lr           CV -
  └─ bjs-3  done       more trees         CV 0.79
  ```
- **mermaid 형식**: 모든 부모에서 화살표를 그리고, 진행 중이거나 중단된 실험은 점선으로 표시합니다.
- **JSON**: `{"project": ..., "score": <점수 이름 또는 null>, "experiments": [{"id", "title", "status", "parents", "origin", "score"}, ...]}`

### `melog export`

프로젝트의 개요, 계보, 실험 기록을 한 문서로 모읍니다. 보고서를 쓸 때의 재료로 씁니다.

| 옵션 | 설명 |
|---|---|
| `-o`, `--output FILE` | 표준 출력 대신 파일에 씁니다 |
| `--status STATUS` | 이 상태의 실험만 모읍니다. 계보도 모은 실험만으로 그립니다 |
| `--with-diffs` | 실험마다 바뀐 파일의 목록과 바뀐 줄 수를 넣습니다. 시작 스냅샷이 없는 실험은 비교할 수 없다고 적습니다 |
| `--score NAME` | 계보의 노드에 보여 줄 점수. `lineage`와 같습니다 |

- **Markdown 구성**: `# <프로젝트> 실험 기록` 아래에 `## 개요`, `## 계보`(mermaid), `## 실험 기록`이 이어집니다. 실험 기록은 시간 순서이고, 실험마다 `### ID · 제목`, 상태/시각/부모/출처/진입점/점수/판정의 표, 본문 네 절(`####`), 코드 변경(`--with-diffs`)이 들어갑니다.
- **출력**: `-o`를 주면 `Wrote 3 experiments of 'lgbm' to experiments.md.`를 출력합니다.
- **JSON**: `{"project": <설정>, "overview": ..., "experiments": [<실험 객체>, ...]}`. `--with-diffs`를 주면 실험 객체마다 `diff_stat` 키가 더해집니다.

---

## 코드 명령

### `melog diff ID [PATH]...`

실험에서 바뀐 코드를 보여 줍니다. 변경점 절을 쓸 때 참고하는 용도입니다.

| 인자, 옵션 | 설명 |
|---|---|
| `ID` | 실험 |
| `PATH...` | 이 경로만 비교합니다 |
| `--against OTHER` | 비교 기준을 바꿉니다. 이 프로젝트의 실험 id이면 그 실험의 종료 스냅샷을, 그렇지 않으면 git 리비전을 씁니다 |
| `--entry-points` | 파일 경로가 아니라 두 실험의 진입점끼리 비교합니다. `--against`에 실험 id가 필요하고, 두 실험의 진입점 개수가 같아야 하며, 기록된 순서대로 짝을 짓습니다 |
| `--stat` | 바뀐 파일의 목록과 바뀐 줄 수만 보여 줍니다 |
| `--raw` | 노트북도 원래의 JSON 그대로 비교합니다 |

| 상황 | 비교 기준 → 비교 대상 |
|---|---|
| 완료되거나 중단된 실험 | 시작 스냅샷 → 종료 스냅샷 |
| 진행 중인 실험 | 시작 스냅샷 → 현재 작업 트리 |
| `--against`를 줌 | OTHER → 이 실험의 코드 |

- **노트북**: 기본적으로 코드 셀의 코드만 비교하고, 머리글은 `diff --melog a/... b/... (code cells only)`입니다. 실행 결과나 실행 번호만 바뀐 노트북은 결과에 나오지 않습니다. JSON으로 읽을 수 없는 노트북은 일반 텍스트로 비교합니다.
- **출력**: 첫 줄에 `# <비교 기준> -> <비교 대상>`을 보여 주고, 그 아래에 unified diff를 보여 줍니다. 바뀐 것이 없으면 `No differences.`를 출력합니다. 터미널에서는 색을 입히고 git의 페이저(`GIT_PAGER`, `core.pager`)로 보여 줍니다.
- **오류**: 시작 스냅샷이 없는데 `--against`가 없음(사후 등록하거나 가져온 실험), `--against`의 실험이 진행 중임, 알 수 없는 리비전, 진입점을 짝지을 수 없음, `--entry-points`와 `PATH`를 함께 줌
- **JSON**: `{"base": {"kind", "label", "commit"}, "target": {...}, "files": [{"path", "old_path", "status", "additions", "deletions", "diff"}]}`. `kind`는 `start_snapshot`, `end_snapshot`, `work_tree`, `experiment`, `revision` 중 하나입니다.

### `melog checkout ID [PATH]`

실험의 코드를 별도의 디렉터리에 꺼냅니다. 현재 작업 공간은 건드리지 않습니다.

| 인자, 옵션 | 설명 |
|---|---|
| `ID` | 실험 |
| `PATH` | 꺼낼 위치. 생략하면 저장소 옆의 `<저장소 이름>-<id>` 디렉터리입니다 |
| `--start` | 종료 스냅샷 대신 시작 스냅샷을 꺼냅니다 |

- **동작**: 스냅샷 커밋을 detached 상태의 git worktree로 꺼냅니다. 꺼낸 디렉터리는 하나의 작업 공간이므로, 그 안에서 바로 새 실험을 시작할 수 있습니다. git이 무시하는 파일(데이터, 모델 가중치 등)은 들어가지 않습니다.
- **오류**: 경로가 이미 있음, 진행 중인 실험인데 `--start`가 없음, 해당 스냅샷이 없음
- **출력**:
  ```
  Checked out the end of bjs-3 into ../titanic-bjs-3
    Files ignored by git, such as data and model weights, are not included.
    It is a separate workspace: you can start a new experiment there.
    Remove it when you are done with 'git worktree remove ../titanic-bjs-3'.
  ```
- **JSON**: `{"project", "id", "snapshot": "start" 또는 "end", "commit", "path"}`

---

## 협업 명령

### `melog sync`

원격 저장소를 통해 모든 melog 프로젝트의 실험 기록과 코드 스냅샷을 공유합니다.

| 옵션 | 설명 |
|---|---|
| `--remote NAME` | 동기화할 git 원격 저장소. 기본값은 `origin`입니다 |
| `--prefer local\|remote` | 모든 충돌을 묻지 않고 이쪽 값으로 해결합니다 |
| `--yes` | 처음 동기화할 때 확인 없이 코드 스냅샷을 올립니다 |

- **동작**: 원격 저장소에서 가져오기, id 충돌 해결, 병합, 한 번에 올리기의 순서로 진행합니다. 그 사이에 다른 사람이 먼저 올렸다면 처음부터 다시 시도합니다(최대 3번).
- **id 충돌**: 같은 id의 실험 기록이 양쪽에 따로 만들어졌다면, 아직 공유되지 않은 로컬 쪽의 id를 다음 순번으로 바꿉니다. 그 실험을 부모로 선언한 로컬 실험, 스냅샷 보존용 참조, 이 clone의 모든 worktree의 진행 중인 실험 정보도 함께 고칩니다.
- **병합**: 본문의 각 절과 개요는 줄 단위로 병합합니다. 나머지 항목(제목, 판정, 이름별 점수, 부모, 진입점, 설정, melog가 남긴 사실)은 항목 전체를 하나의 단위로 비교합니다.
- **충돌**: 터미널에서는 충돌한 항목마다 로컬 값과 원격 값을 보여 주고 `Keep [l]ocal or [r]emote?`라고 묻습니다. 터미널이 아니면, `--prefer`가 없는 한 아무것도 바꾸지 않고 충돌 목록과 함께 오류로 종료합니다.
- **처음 올릴 때**: 원격 저장소마다 처음 한 번, 올라갈 스냅샷의 개수와 한 번도 커밋되지 않은 파일의 목록을 보여 주고 확인을 받습니다. 거절하면 `Sync cancelled. Nothing was uploaded.`를 출력하고 종료 코드 1로 끝납니다. 터미널이 아니면 `--yes`가 필요합니다.
- **오류**: 원격 저장소가 없음(원격 저장소 목록을 보여 줌), 해결하지 않은 충돌, 처음 올리는데 확인을 받을 수 없음, 원격 저장소가 계속 바뀜, 올리기 실패
- **출력**:
  ```
  Synced with origin.
    received: lgbm/bjs-4
    updated:  lgbm/bjs-1
    sent:     lgbm/bjs-5
    sent changes to: lgbm/bjs-2
    resolved: lgbm/bjs-1 verdict (kept local)
    renamed:  your lgbm/bjs-7 is now lgbm/bjs-9, because another clone also used bjs-7
  ```
  해당하는 내용이 있는 줄만 보여 주고, 아무것도 없으면 `Already in sync with origin.`을 출력합니다.
- **JSON**: `{"remote", "received", "updated", "sent", "sent_changes", "resolved": [{"where", "field", "kept"}], "renamed": [{"project", "old_id", "new_id"}]}`

---

## 설정과 저장 위치

### git 설정

| 키 | 범위 | 설명 |
|---|---|---|
| `melog.initials` | 전역(권장) | 실험 id에 쓰는 이니셜. 영문 소문자와 숫자만 쓸 수 있습니다. 저장소별로 다르게 쓰려면 로컬 설정에 둡니다 |
| `melog.sharedremote` | 로컬 | 처음 업로드를 확인받은 원격 저장소의 이름. melog가 기록합니다 |

### 참고하는 git 설정과 환경 변수

| 이름 | 쓰는 곳 |
|---|---|
| `GIT_EDITOR`, `core.editor`, `VISUAL`, `EDITOR` | `edit`, `project edit`의 에디터 |
| `GIT_PAGER`, `core.pager` | `diff`의 페이저. `LESS`가 없으면 git처럼 `FRX`를 씁니다 |
| `user.name`, `user.email` | melog가 만드는 커밋의 작성자. 없으면 `melog <melog@localhost>`를 씁니다 |

### 저장 위치

| 대상 | 위치 |
|---|---|
| 실험 기록, 설정, 개요 | `refs/melog/store`가 가리키는 커밋 안의 `<프로젝트>/records/<id>.md`, `<프로젝트>/project.yaml`, `<프로젝트>/overview.md` |
| 코드 스냅샷 보존용 참조 | `refs/melog/snapshots/<프로젝트>/<id>/start`, `.../end` |
| 원격 저장소의 상태 | `refs/melog/remotes/<원격 저장소>/...` |
| 작업 공간의 진행 중인 실험 | 작업 공간마다 있는 git 내부 디렉터리의 `melog-workspace.json` |
