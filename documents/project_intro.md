# 项目技术栈

本项目是一个 FastAPI 构建的 AI 应用后端。技术栈包括：FastAPI 提供 Web 接口，SQLAlchemy 2.0 异步 ORM 管理数据库（PostgreSQL/SQLite），ChromaDB 做向量检索，Redis 做 Token 黑名单缓存，DeepSeek 大模型提供对话能力，Docker 容器化部署在 Railway 平台，日志使用 loguru 输出 JSON 结构化日志。

# 项目架构

项目采用 Router 到 Service 到 DAO 的分层架构：API 路由层负责参数校验和 HTTP 响应，Service 层承载业务逻辑，数据访问统一由 DAO 层封装。数据库操作使用统一事务边界管理，配合全局异常处理器，任何接口出错都会返回统一的错误结构，不会把堆栈泄漏给前端。

# 认证机制

认证采用 JWT 双 Token 方案：登录返回短效 access_token 和长效 refresh_token，access 过期后用 refresh 换新 token 无需重新登录。登出时把 access_token 加入 Redis 黑名单，失效时间等于 token 剩余有效期。Redis 故障时自动降级，跳过黑名单检查但依然通过数据库验证用户身份，保证服务可用性。

# RAG 检索

RAG 使用 ChromaDB 向量库，嵌入模型是 BAAI/bge-large-zh-v1.5，检索空间为余弦相似度。检索结果做距离归一化得到 0 到 1 的相似度分数，分数低于 0.6 阈值时认为没有相关文档，自动降级为纯大模型回答，避免把不相关的内容喂给模型产生幻觉。

# Agent 实现

Agent 是手写实现的 ReAct 推理循环，没有用 LangChain 框架：模型输出 Thought、Action、Action Input 三行，代码解析后调用对应工具，把工具结果拼回对话继续推理，最多迭代 5 轮。内置 Token 累计熔断，累计消耗超过预算立即停止，防止失控调用。工具包括 RAG 检索、网络搜索、计算器、Python 执行器（模块白名单沙箱）。
