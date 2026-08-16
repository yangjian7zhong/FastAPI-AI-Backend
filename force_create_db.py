import sqlite3
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base

# 1. 连接数据库（使用同步引擎，避免异步麻烦）
DATABASE_URL = "sqlite:///./test.db"   # 和你的项目同一个文件
engine = create_engine(DATABASE_URL, echo=True)

# 2. 定义模型
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Integer, default=0)   # SQLite 没有布尔，用整数

# 3. 建表（如果表已存在，不会删除数据）
Base.metadata.create_all(engine)

# 4. 验证表是否存在
conn = sqlite3.connect("test.db")
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
if cursor.fetchone():
    print("✅ users 表已存在")
else:
    print("❌ 表创建失败")

conn.close()
print("完成")