import sqlite3

# 1. 连接数据库（如果不存在会自动创建）
conn = sqlite3.connect("test.db")
cursor = conn.cursor()

# 2. 执行建表 SQL
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT 0 NOT NULL
)
""")

# 3. 提交并关闭
conn.commit()
conn.close()

print("✅ users 表创建成功")