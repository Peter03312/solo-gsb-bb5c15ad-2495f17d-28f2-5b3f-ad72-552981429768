"""运行期配置，全部来自环境变量，均有默认值以便一键启动。"""
import os


class Settings:
    # SQLite 数据库文件路径（容器内默认放在挂载卷 /data 下）
    db_path: str = os.getenv("DB_PATH", "/data/review.db")
    # 会务方本地密钥；生产环境务必通过环境变量覆盖
    organizer_key: str = os.getenv("ORGANIZER_KEY", "dev-organizer-key")
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    # 求解器回溯节点预算，防止极端输入下无限搜索
    solver_node_budget: int = int(os.getenv("SOLVER_NODE_BUDGET", "500000"))


settings = Settings()
