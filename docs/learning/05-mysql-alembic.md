# 学习笔记 05:Alembic 迁移 + MySQL 对应

## 1. 改了什么
- **Alembic**:表结构不再由 `create_all` 在启动时「有就跳过、没有就建」,而是用 **迁移文件**(`migrations/versions/*.py`)按版本管理
  - 应用启动时自动 `upgrade head`(`app/migrate.py`)
  - 以前用 create_all 建的库(没有版本记录)→ 先 `stamp head` 记录基准,再升级
- **MySQL**:加了 `aiomysql` 驱动、`docker-compose.yml`(MySQL 8.4,utf8mb4,只监听本机)、连接池的 `pool_recycle`
- 测试:「空库 upgrade head 后和 ORM 模型没有差异」→ 改了 models.py 却忘了生成迁移,测试就会失败

## 2. 为什么需要迁移(这次亲身经历过)
认证改造时 users 表删掉了 token 列,但 `create_all` **不会修改已存在的表** → 旧 live.db 里还留着 NOT NULL 的 token 列 → 注册失败。
迁移工具就是为了解决「代码里的表结构」和「实际数据库里的表结构」不一致的问题,而且不用删数据。

## 3. 和学过的内容的对应
| 这里 | 你学过的 |
|---|---|
| 迁移文件里的 `op.create_table(...)` | DB2x 的 `CREATE TABLE`、主键、外键、NOT NULL 约束 |
| `alembic upgrade head --sql` 输出的 DDL | 就是你在 MySQL 命令行里写的 SQL |
| docker-compose 的 MySQL | DB2x 的 XAMPP MySQL + empdb 导入 |
| utf8mb4 | 日文・emoji 要 4 字节 UTF-8,MySQL 旧的 `utf8` 只有 3 字节 |

## 4. 常用命令
```bash
alembic revision --autogenerate -m "add phone to users"   # 比较 models.py 和 DB,生成迁移
alembic upgrade head        # 升到最新
alembic downgrade -1        # 回退一个版本
alembic history             # 看历史
alembic upgrade head --sql  # 不连 DB,只输出 SQL(检查用)
```
注意:autogenerate 生成后**一定要读一遍**再提交,它猜不出「改名」(会变成删除+新增,数据丢失)。

## 5. 面试会问
- **为什么照合顺序用 utf8mb4_bin?** 默认的 `utf8mb4_0900_ai_ci` 不区分大小写和重音(ai = accent insensitive),比如 `Taro` 和 `taro` 会被当成同一个用户名;用 bin(按字节比较)和 SQLite 的行为一致。
- **SQLite 的迁移为什么要 batch 模式?** SQLite 的 ALTER TABLE 不能删列/改列,Alembic 用「建新表 → 复制数据 → 删旧表」来实现。
- **启动时自动迁移有什么风险?** 多台服务器同时启动可能同时迁移;大表迁移会拖慢启动。规模大了以后,应该在部署流程里单独执行 `alembic upgrade head`,并设置 `AUTO_MIGRATE=false`。

## 6. 在真实 MySQL 上的验证(已完成)
- GitHub Actions 的 unit-mysql 任务,以及本机 Docker Desktop(2026-11-13)上都验证过:
  - `docker compose up -d db` → MySQL 8.4.13 / utf8mb4 / utf8mb4_bin
  - 应用连接 MySQL 启动 → Alembic 自动迁移、写入示例商品
  - E2E 41/41、pytest 39/39 通过(并发下单在 MySQL 的行锁下也是「只成功 1 单」)
- 自己用 MySQL 开发时:`.env` 的 `DATABASE_URL` 改成 `mysql+aiomysql://liveuser:livepass@127.0.0.1:3306/livecommerce?charset=utf8mb4`。不用时 `docker compose stop db`。

## 7. 自测问题
1. `create_all` 和 Alembic 迁移的区别是什么?
2. 给 users 表加一个 `nickname` 列,完整的步骤是什么?
3. `stamp head` 是做什么的?什么时候用?
4. MySQL 的 `utf8` 和 `utf8mb4` 有什么区别?
