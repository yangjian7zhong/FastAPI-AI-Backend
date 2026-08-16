import logging
from redis.asyncio import Redis
from app.core.config import settings

logger = logging.getLogger(__name__)

class RedisClient:
    def __init__(self):
        self.client = None
        self._connected = False
        self.last_error = None  # 最近一次连接失败原因（诊断用）

    async def connect(self):
        if not self._connected:
            # 三连试：REDIS_URL 原样 → REDIS_URL+TLS（Upstash 等托管 Redis 强制 TLS）→ 独立 host/port 配置
            attempts = []
            if settings.REDIS_URL:
                attempts.append(("REDIS_URL", Redis.from_url(
                    settings.REDIS_URL,
                    decode_responses=True,
                    protocol=2,  # 强制使用 RESP2 协议
                    socket_connect_timeout=5,
                    socket_timeout=5,
                )))
                # redis-py 5.x 的 from_url 不支持 ssl= 参数，改用 rediss:// 原生 TLS 协议
                tls_url = settings.REDIS_URL
                if tls_url.startswith("redis://"):
                    tls_url = "rediss://" + tls_url[len("redis://"):]
                attempts.append(("REDIS_URL+TLS", Redis.from_url(
                    tls_url,
                    decode_responses=True,
                    protocol=2,
                    socket_connect_timeout=5,
                    socket_timeout=5,
                )))
            attempts.append(("HOST/PORT", Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
                decode_responses=True,
                protocol=2,  # 强制使用 RESP2 协议
                socket_connect_timeout=5,
                socket_timeout=5,
            )))
            last_err = None
            for label, client in attempts:
                try:
                    await client.ping()
                    self.client = client
                    self._connected = True
                    self.last_error = None
                    logger.info(f"✅ Redis 连接成功（{label}）")
                    return
                except Exception as e:
                    last_err = e
                    logger.warning(f"⚠️ Redis 连接失败（{label}）: {e}")
            self._connected = False
            self.last_error = str(last_err)
            logger.warning(f"⚠️ Redis 全部连接方式失败，将进入降级模式: {last_err}")

    async def get(self, key: str):
        if not self._connected:
            logger.warning(f"⚠️ Redis 降级: get({key}) 失败，返回 None")
            return None
        try:
            return await self.client.get(key)
        except Exception as e:
            logger.warning(f"⚠️ Redis 降级: get({key}) 异常，返回 None: {e}")
            return None

    async def setex(self, key: str, time: int, value: str):
        if not self._connected:
            logger.warning(f"⚠️ Redis 降级: setex({key}) 未执行")
            return
        try:
            await self.client.setex(key, time, value)
        except Exception as e:
            logger.warning(f"⚠️ Redis 降级: setex({key}) 失败: {e}")

    async def delete(self, key: str):
        if not self._connected:
            logger.warning(f"⚠️ Redis 降级: delete({key}) 未执行")
            return
        try:
            await self.client.delete(key)
        except Exception as e:
            logger.warning(f"⚠️ Redis 降级: delete({key}) 失败: {e}")

    async def exists(self, key: str) -> bool:
        """判断 key 是否存在（token 黑名单校验用）。
        Redis 故障时返回 False 并降级：由上层 DB 验证兜底。"""
        if not self._connected:
            logger.warning(f"⚠️ Redis 降级: exists({key}) 返回 False（由 DB 验证兜底）")
            return False
        try:
            return bool(await self.client.exists(key))
        except Exception as e:
            logger.warning(f"⚠️ Redis 降级: exists({key}) 异常，返回 False: {e}")
            return False

redis_client = RedisClient()