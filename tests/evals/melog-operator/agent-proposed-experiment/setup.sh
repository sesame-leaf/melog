#!/usr/bin/env bash
# Builds the test repository in the current directory. Needs melog on PATH.
set -eu
git init -q -b main
git config user.name "Tester"
git config user.email "tester@example.com"
git config melog.initials bjs
printf "data/\n*.csv\n" > .gitignore
cat > train.py <<'PY'
import lightgbm as lgb

LEARNING_RATE = 0.1
NUM_LEAVES = 31


def train(X, y):
    params = {"learning_rate": LEARNING_RATE, "num_leaves": NUM_LEAVES}
    return lgb.train(params, lgb.Dataset(X, y))
PY
git add . && git commit -q -m "baseline training script"
melog init lgbm --json > /dev/null
melog start "LightGBM baseline" --motivation "공개 노트북의 기본 설정으로 베이스라인을 만든다" --entry train.py --json > /dev/null
melog done --score CV=0.812 --score "CV std=0.004" --verdict "베이스라인으로 쓸 만함" --json > /dev/null
