import asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent.delivery.http.dashboard.router import router
from agent.modules.channels.public import ChannelManager

async def idle_runner() -> None:
    return None

app = FastAPI()
app.state.channel_manager = ChannelManager()
app.include_router(router)

for route in app.routes:
    print(route.path)

client = TestClient(app)
response = client.get("/dashboard/services")
print(response.status_code)
print(response.text)
