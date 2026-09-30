#!/usr/bin/env bash
# 端到端调用示例。
# 用法：
#   BASE=http://127.0.0.1:8000 ORGANIZER_KEY=dev-organizer-key bash examples/api_examples.sh
set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8000}"
KEY="${ORGANIZER_KEY:-dev-organizer-key}"
AUTH=(-H "X-Organizer-Key: ${KEY}")
j() { python3 -c "import sys,json;print(json.dumps(json.load(sys.stdin),ensure_ascii=False,indent=2))"; }

echo "== 1. 录入论文 =="
curl -sS -X POST "${BASE}/papers" "${AUTH[@]}" -H 'Content-Type: application/json' -d '{
  "paper_id":"P1","anonymous_text":"匿名稿正文 P1 ...","topic":"ML","institution":"UniX"
}' | j
curl -sS -X POST "${BASE}/papers" "${AUTH[@]}" -H 'Content-Type: application/json' -d '{
  "paper_id":"P2","anonymous_text":"匿名稿正文 P2 ...","topic":"DB","institution":"UniY"
}' | j

echo "== 2. 录入评审人（编号/凭据/擅长主题/机构/容量/回避论文） =="
curl -sS -X POST "${BASE}/reviewers" "${AUTH[@]}" -H 'Content-Type: application/json' -d '{
  "reviewer_id":"R1","credential":"secret-r1","topics":["ML"],"institution":"UniA",
  "capacity":2,"excluded_papers":[]
}' | j
curl -sS -X POST "${BASE}/reviewers" "${AUTH[@]}" -H 'Content-Type: application/json' -d '{
  "reviewer_id":"R2","credential":"secret-r2","topics":["ML","DB"],"institution":"UniB",
  "capacity":1,"excluded_papers":[]
}' | j
curl -sS -X POST "${BASE}/reviewers" "${AUTH[@]}" -H 'Content-Type: application/json' -d '{
  "reviewer_id":"R3","credential":"secret-r3","topics":["DB"],"institution":"UniC",
  "capacity":2,"excluded_papers":["P1"]
}' | j
curl -sS -X POST "${BASE}/reviewers" "${AUTH[@]}" -H 'Content-Type: application/json' -d '{
  "reviewer_id":"R4","credential":"secret-r4","topics":["OS"],"institution":"UniD",
  "capacity":2,"excluded_papers":[]
}' | j

echo "== 3. 预演（不发布，返回 material_rev/完整方案/未分配原因/最高负载比例） =="
DRY=$(curl -sS -X POST "${BASE}/assignments/dry-run" "${AUTH[@]}")
echo "${DRY}" | j
REV=$(echo "${DRY}" | python3 -c "import sys,json;print(json.load(sys.stdin)['material_rev'])")

echo "== 4. 携带预演修订号发布（资料变动会以 409 拒绝） =="
curl -sS -X POST "${BASE}/assignments/publish" "${AUTH[@]}" \
  -H 'Content-Type: application/json' \
  -d "{\"material_rev\":${REV}}" | j

echo "== 5. 会务方查看发布版与排除原因 =="
curl -sS "${BASE}/assignments" "${AUTH[@]}" | j

echo "== 6. 评审人凭自身凭据读取已分配匿名稿（Basic Auth: 编号:凭据） =="
curl -sS -u R1:secret-r1 "${BASE}/reviewer/me/papers" | j
echo "-- 错误凭据应返回 401 --"
curl -sS -o /dev/null -w "%{http_code}\n" -u R1:wrong "${BASE}/reviewer/me/papers"

echo "== 7. 非法容量 / 重复编号 的明确报错 =="
curl -sS -X POST "${BASE}/reviewers" "${AUTH[@]}" -H 'Content-Type: application/json' -d '{
  "reviewer_id":"BAD","credential":"x","topics":["ML"],"institution":"U","capacity":-3
}' | j
curl -sS -X POST "${BASE}/reviewers" "${AUTH[@]}" -H 'Content-Type: application/json' -d '{
  "reviewer_id":"R1","credential":"x","topics":["ML"],"institution":"U","capacity":1
}' | j
