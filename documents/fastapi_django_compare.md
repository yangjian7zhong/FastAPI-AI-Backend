# FastAPI 与 Django 对比

FastAPI 和 Django 是两种定位不同的 Python Web 框架。FastAPI 基于 Starlette 和 Pydantic，原生支持异步，性能高，自动生成 OpenAPI 文档，适合构建高性能 API 服务和 AI 应用后端；Django 是重量级全栈框架，自带 ORM、Admin 后台、模板系统、认证体系，适合快速搭建完整业务站点。FastAPI 的异步特性在 AI 场景尤其重要，因为大模型调用是 IO 密集型操作，异步可以让单进程同时处理大量并发请求，而 Django 默认的同步 WSGI 模型在高并发 IO 场景容易阻塞。

# 选型建议

选 FastAPI 的场景：纯 API 后端、AI 应用、微服务、需要高并发和流式响应（SSE 流式输出）。选 Django 的场景：需要 Admin 管理后台的内容型站点、快速搭建完整的 MVC 业务系统、团队熟悉 Django 生态。两者也经常搭配使用，Django 管业务后台，FastAPI 管 AI 接口。
