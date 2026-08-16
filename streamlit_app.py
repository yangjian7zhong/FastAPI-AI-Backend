import streamlit as st
import asyncio
import httpx

st.set_page_config(page_title="AI 问答助手", layout="wide")
st.title("🤖 AI 智能问答")

# 后端 API 地址（部署时改成你的 Railway 域名）
API_BASE = "https://fastapi-auth-service-production-87f3.up.railway.app"


async def post_json(url, **kwargs):
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.post(url, **kwargs)

with st.sidebar:
    st.header("🔑 登录")
    username = st.text_input("用户名")
    password = st.text_input("密码", type="password")
    if st.button("登录"):
        resp = asyncio.run(post_json(f"{API_BASE}/api/v1/login", json={
            "username": username,
            "password": password
        }))
        if resp.status_code == 200:
            st.session_state.token = resp.json()["access_token"]
            st.success("登录成功")
        else:
            st.error("登录失败")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("输入问题"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    if "token" not in st.session_state:
        st.warning("请先登录")
    else:
        with st.chat_message("assistant"):
            with st.spinner("思考中..."):
                resp = asyncio.run(post_json(
                    f"{API_BASE}/api/v1/chat",
                    headers={"Authorization": f"Bearer {st.session_state.token}"},
                    json={"prompt": prompt}
                ))
                if resp.status_code == 200:
                    content = resp.json().get("content", "无回复")
                    st.markdown(content)
                    st.session_state.messages.append({"role": "assistant", "content": content})
                else:
                    st.error(f"请求失败: {resp.status_code}")
                    st.text(resp.text)  
