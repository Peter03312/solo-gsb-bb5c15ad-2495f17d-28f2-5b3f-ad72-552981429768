# 双盲论文评审分配服务

Python 3.11 + FastAPI + SQLite。会务方凭**本地密钥**维护论文与评审人资料、
执行**预演**与**发布**；评审人凭**个人凭据**只读自己被分配到的匿名稿。

---

## 1. 一键启动（Docker Compose）

```bash
cp .env.example .env          # 修改 ORGANIZER_KEY 为强随机密钥
docker compose up --build -d
```

- 首次启动自动建表、初始化 SQLite（无需手工迁移）。
- 数据持久化在命名卷 `review-data`（容器内 `/data/review.db`，含 WAL 文件）。
- 停止 / 查看日志：

```bash
docker compose logs -f
docker compose down            # 保留数据
docker compose down -v         # 连同数据卷一起删除
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","material_rev":1}
```

## 2. 本地直接运行（不使用 Docker）

```bash
python3 -m venv --without-pip .venv && python3 -m ensurepip  # 或已带 pip 的 venv
pip install -r requirements.txt
DB_PATH=./review.db ORGANIZER_KEY=dev-organizer-key \
  uvicorn app.main:app --host 0.0.0.0 --port 8000
```

交互式文档（Swagger UI）：<http://127.0.0.1:8000/docs>

## 3. 配置（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ORGANIZER_KEY` | `dev-organizer-key` | 会务方本地密钥，通过请求头 `X-Organizer-Key` 传递；**生产必须覆盖** |
| `DB_PATH` | `/data/review.db` | SQLite 数据库文件路径，其目录需可写 |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | 监听地址与端口；Compose 用宿主机 `PORT` 映射容器 8000 |
| `SOLVER_NODE_BUDGET` | `500000` | 单次求解回溯节点预算，防止极端输入长时间搜索 |

- 服务地址：`http://<主机>:8000`（Compose 默认 `http://127.0.0.1:8000`）
- 评审人凭据使用 HTTP Basic Auth：用户名=评审人编号，密码=录入时的凭据；
  库内仅保存 PBKDF2 哈希，不存明文。

## 4. 业务规则

每篇论文必须分配 **2 名评审人**，且：

1. 两名评审人**互为不同机构**，且都**不得与作者同机构**；
2. 其中**至少 1 人擅长**该论文主题；
3. 不得分配给**主动回避该论文**的评审人；
4. 评审人已分配论文数**不得超过其容量**（容量 0 不参与分配）。

择优顺序（存在多个可行方案时）：

1. **最小化最高已用容量比例** `max(已用_i / 容量_i)`；
2. 在此前提下，按论文编号升序、每篇两名评审人编号升序拼成扁平序列，
   取**字典序最小**的全局方案。

不存在完整方案时，预演返回 `complete=false`、每个未分配论文编号及**限制原因**
（同机构回避、主动回避、容量为 0/已满、无主题专家、异机构可用人数不足等），
且**已发布版本保持不变**、部分方案不会被发布。

### 修订号与发布的并发安全

- 论文/评审人的每次新增、修改、删除都会让资料修订号 `material_rev` 自增。
- 预演返回当时的 `material_rev`；发布请求**必须原样携带**该修订号。
- 发布在单个 `BEGIN IMMEDIATE` 写事务中完成「**核对修订号 → 用同一快照重算
  → 要求完整 → 原子替换发布表**」。资料一旦变动，旧修订号的发布被拒绝
  （`409 revision_mismatch`），因此**资料修改与发布并发时不可能发布旧版方案**。
- 评审人读到的始终是**最近一次成功发布**的方案；资料再怎么改，发布版不变，
  直到携带最新修订号的新方案发布成功。

## 5. HTTP API

会务方接口均需 `-H "X-Organizer-Key: $ORGANIZER_KEY"`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查（无需鉴权） |
| GET | `/material` | 查看全部论文/评审人与当前修订号 |
| POST | `/papers` | 录入论文（编号重复 → 409） |
| PUT | `/papers/{paper_id}` | 修改论文 |
| DELETE | `/papers/{paper_id}` | 删除论文（同步清理其回避引用与分配） |
| POST | `/reviewers` | 录入评审人（编号重复、凭据重复 → 409；容量负数 → 422） |
| PUT | `/reviewers/{reviewer_id}` | 修改评审人 |
| DELETE | `/reviewers/{reviewer_id}` | 删除评审人 |
| POST | `/assignments/dry-run` | 预演：返回最优方案或未分配原因，**不落库** |
| POST | `/assignments/publish` | 发布：body `{"material_rev": <预演修订号>}` |
| GET | `/assignments` | 会务方查看发布版、是否过期(stale)及排除原因 |
| GET | `/reviewer/me/papers` | 评审人 Basic Auth 读取本人被分配的匿名稿 |

### 论文对象

```json
{ "paper_id": "P1", "anonymous_text": "匿名稿全文",
  "topic": "ML", "institution": "UniX" }
```

### 评审人对象

```json
{ "reviewer_id": "R1", "credential": "secret-r1",
  "topics": ["ML", "DB"], "institution": "UniA",
  "capacity": 2, "excluded_papers": ["P9"] }
```

`capacity` 必须是非负整数；`excluded_papers` 中的编号必须已存在，否则 400。

## 6. 调用示例

完整可执行脚本：[`examples/api_examples.sh`](examples/api_examples.sh)

```bash
BASE=http://127.0.0.1:8000 ORGANIZER_KEY=dev-organizer-key \
  bash examples/api_examples.sh
```

关键片段：

```bash
# 录入
curl -sS -X POST localhost:8000/papers -H "X-Organizer-Key: $K" \
  -H 'Content-Type: application/json' \
  -d '{"paper_id":"P1","anonymous_text":"...","topic":"ML","institution":"UniX"}'

# 预演 -> 记下 material_rev
curl -sS -X POST localhost:8000/assignments/dry-run -H "X-Organizer-Key: $K"

# 发布（用预演返回的修订号）
curl -sS -X POST localhost:8000/assignments/publish -H "X-Organizer-Key: $K" \
  -H 'Content-Type: application/json' -d '{"material_rev":6}'

# 评审人取自己的稿
curl -sS -u R1:secret-r1 localhost:8000/reviewer/me/papers
```

错误响应统一形如：

```json
{ "error": { "code": "revision_mismatch",
             "message": "资料已变动（预演修订号=6，当前修订号=7）……",
             "details": {"dry_run_rev": 6, "current_rev": 7} } }
```

典型错误码：`duplicate_paper_id` / `duplicate_reviewer_id` /
`duplicate_credential` / `validation_error`（非法容量等）/
`invalid_organizer_key` / `invalid_credential` /
`excluded_paper_not_found` / `revision_mismatch` / `no_complete_solution`。

## 7. 测试

```bash
pip install pytest httpx
python -m pytest -q
```

包含：求解器在 150 组随机小实例上与暴力枚举结果逐一对照
（最小峰值比例 + 字典序最优）、修订号失效拒绝发布、凭据隔离、回避/机构/容量
约束、非法容量与重复编号报错等端到端用例。
