# app/skills/amap_skills.py

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.services.tool_service import ToolService
from app.core.agentic.agent_constants import AgentToolNames, AgentToolDesc

class RouteInput(BaseModel):
    origin_name: str = Field(description=AgentToolDesc.MAP_ROUTE_ORIGIN)
    destination_name: str = Field(description=AgentToolDesc.MAP_ROUTE_DEST)
    mode: str = Field(default="driving", description=AgentToolDesc.MAP_ROUTE_MODE)

class NearbyInput(BaseModel):
    keyword: str = Field(description=AgentToolDesc.MAP_NEARBY_KEYWORD)
    city: str = Field(default="全国", description=AgentToolDesc.MAP_NEARBY_CITY)

class WeatherInput(BaseModel):
    city_name: str = Field(description=AgentToolDesc.MAP_WEATHER_CITY)

def create_amap_tools():
    """
    工厂函数：返回高德地图相关工具的集合
    """
    async def expert_get_route(origin_name: str, destination_name: str, mode: str = "driving") -> str:
        # 调用底层服务，并直接返回序列化结果供 Agent 后续流式解析
        return await ToolService.get_route_plan(origin_name, destination_name, mode)

    async def expert_search_nearby(keyword: str, city: str = "全国") -> str:
        return await ToolService.search_nearby(keyword, city)

    async def expert_get_weather(city_name: str) -> str:
        return await ToolService.get_weather(city_name)

    route_tool = StructuredTool.from_function(
        coroutine=expert_get_route,
        name=AgentToolNames.MAP_ROUTE,
        description=AgentToolDesc.MAP_ROUTE,
        args_schema=RouteInput,
    )

    nearby_tool = StructuredTool.from_function(
        coroutine=expert_search_nearby,
        name=AgentToolNames.MAP_NEARBY,
        description=AgentToolDesc.MAP_NEARBY,
        args_schema=NearbyInput,
    )

    weather_tool = StructuredTool.from_function(
        coroutine=expert_get_weather,
        name=AgentToolNames.MAP_WEATHER,
        description=AgentToolDesc.MAP_WEATHER,
        args_schema=WeatherInput,
    )

    return [route_tool, nearby_tool, weather_tool]