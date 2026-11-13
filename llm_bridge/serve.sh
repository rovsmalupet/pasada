#!/bin/bash
# The server side of the hand-over (run it in tmux): every minute, pull the simulation's new input
# files, answer them with the model, and push the answers back.
#   tmux new -d -s bridge "bash ~/abm/llm_bridge/serve.sh > ~/bridge_log.txt 2>&1"
cd "$(dirname "$0")/.." || exit 1
PY=${PY:-$HOME/thesis-env/bin/python}
MODEL=${MODEL:-qwen14}
echo "bridge started $(date) | model $MODEL | repo $(pwd) | branch $(git branch --show-current)"
while true; do
  git pull -q --rebase --autostash || echo "$(date +%H:%M) git pull failed; trying again in a minute"
  $PY -u -m llm_bridge.answer --model "$MODEL" | grep --line-buffered -v "^Nothing to answer"
  git add starosa_bayan/demand
  if ! git diff --cached --quiet; then
    git commit -q -m "LLM answers ($MODEL) $(date +%F_%H%M)"
    git push -q && echo "$(date +%H:%M) pushed the answers" || echo "$(date +%H:%M) push failed; will retry"
  fi
  git log origin/$(git branch --show-current)..HEAD --oneline | grep -q . && git push -q
  sleep 60
done
