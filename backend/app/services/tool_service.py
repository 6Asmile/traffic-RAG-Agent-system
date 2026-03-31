# app/services/tool_service.py

import httpx
import logging
import json
import urllib.parse
from app.core.config import settings
from app.core.constants import AmapAPI, UIConstants  # 引入常量池
from langchain_core.tools import tool
import asyncio

logger = logging.getLogger(__name__)


class ToolService:

    @staticmethod
    async def _resolve_coordinates(client, api_key, address, city=None):
        """智能坐标解析助手 (支持模糊匹配)"""
        try:
            # 1. 尝试 POI 搜索
            res = await client.get(AmapAPI.PLACE_TEXT,
                                   params={"key": api_key, "keywords": address, "city": city, "offset": 1,
                                           "extensions": "all"})
            data = res.json()
            if data.get('status') == '1' and data.get('pois'):
                top_poi = data['pois'][0]
                return top_poi['location'], top_poi['name'], top_poi['adcode']

            # 2. 尝试地理编码
            res = await client.get(AmapAPI.GEOCODE, params={"key": api_key, "address": address})
            data = res.json()
            if data.get('status') == '1' and data.get('geocodes'):
                geo = data['geocodes'][0]
                return geo['location'], geo['formatted_address'], geo['adcode']
        except Exception:
            pass

        return None, None, None

    @staticmethod
    async def get_route_plan(origin_name: str, destination_name: str, mode: str = "driving"):
        """✨ 终极互动版路径规划"""
        api_key = settings.AMAP_KEY
        if not api_key: return "❌ 未配置地图 API Key"

        async with httpx.AsyncClient() as client:
            try:
                origin_name = str(origin_name)
                destination_name = str(destination_name)

                o_loc, o_name, o_adcode = await ToolService._resolve_coordinates(client, api_key, origin_name)
                d_loc, d_name, _ = await ToolService._resolve_coordinates(client, api_key, destination_name)

                if not o_loc or not d_loc:
                    return json.dumps({"text_data": f"抱歉，无法精确识别地点 {origin_name} 或 {destination_name}。"},
                                      ensure_ascii=False)

                amap_mode = "car"
                if mode == "transit":
                    amap_mode = "bus"
                elif mode == "walking":
                    amap_mode = "walk"

                safe_o_name = urllib.parse.quote(o_name)
                safe_d_name = urllib.parse.quote(d_name)

                # 使用常量池中的 URI
                interactive_url = (f"{AmapAPI.URI_NAVIGATION}?"
                                   f"from={o_loc},{safe_o_name}&"
                                   f"to={d_loc},{safe_d_name}&"
                                   f"mode={amap_mode}&"
                                   f"view=map&"
                                   f"src=mypage&"
                                   f"coordinate=gaode&"
                                   f"callnative=0")

                html_widget = f"""
<div style="{UIConstants.WIDGET_STYLE}">
    <div style="{UIConstants.HEADER_STYLE}">
        <div style="display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: bold; color: #1d1d1f;">
            <span style="color: #007aff;">📍</span> {o_name[:8]}... 
            <span style="color: #8e8e93; font-weight: normal; margin: 0 4px;">➔</span> 
            <span style="color: #ff3b30;">🏁</span> {d_name[:8]}...
        </div>
        <a href="{interactive_url}" target="_blank" style="flex-shrink:0; font-size: 12px; color: #ffffff; text-decoration: none; padding: 6px 12px; background: #007aff; border-radius: 20px; font-weight: 500;">全屏交互</a>
    </div>
    <div style="width: 100%; height: 500px; overflow-y: auto; -webkit-overflow-scrolling: touch; background: #f8f8f8;">
        <iframe src="{interactive_url}" width="100%" height="100%" frameborder="0" allow="geolocation" style="display: block; min-height: 700px;"></iframe>
    </div>
    <div style="padding: 8px 16px; background: #f9f9f9; font-size: 11px; color: #999; text-align: center; border-top: 1px solid #eee;">
        💡 提示：您可以在地图内直接切换 公交/步行 方案
    </div>
</div>
"""
                text_data = f"起点：{o_name}，终点：{d_name}。我已经向用户展示了互动地图卡片。\n"

                if mode == "driving":
                    resp = await client.get(AmapAPI.DIR_DRIVING,
                                            params={"key": api_key, "origin": o_loc, "destination": d_loc,
                                                    "strategy": 10})
                    data = resp.json()
                    if data.get('status') == '1' and data.get('route', {}).get('paths'):
                        r = data['route']['paths'][0]
                        text_data += f"驾车数据：总距离 {round(int(r['distance']) / 1000, 2)}km，耗时约 {round(int(r['duration']) / 60)} 分钟，打车预估 {data['route'].get('taxi_cost', '未知')} 元。"

                elif mode == "transit":
                    resp = await client.get(AmapAPI.DIR_TRANSIT,
                                            params={"key": api_key, "origin": o_loc, "destination": d_loc,
                                                    "city": o_adcode, "strategy": 0})
                    data = resp.json()
                    if data.get('status') == '1' and data.get('route', {}).get('transits'):
                        plan = data['route']['transits'][0]
                        text_data += f"公交/地铁数据：耗时约 {round(int(plan['duration']) / 60)} 分钟，包含步行 {plan.get('walking_distance', 0)} 米。"

                return json.dumps({"html_widget": html_widget, "text_data": text_data}, ensure_ascii=False)

            except Exception as e:
                logger.error(f"路线规划故障: {e}")
                return json.dumps({"text_data": f"路线规划服务暂时不可用: {str(e)}"}, ensure_ascii=False)

    @staticmethod
    async def search_nearby(keyword: str, city: str = "全国"):
        """✨ 互动版周边搜索雷达"""
        api_key = settings.AMAP_KEY
        if not keyword: return json.dumps({"text_data": "缺少搜索关键词。"}, ensure_ascii=False)

        async with httpx.AsyncClient() as client:
            try:
                import re
                search_query = str(keyword)
                anchor_loc = None
                display_name = search_query

                match = re.search(r"(.+?)(附近|周边)(?:的)?(.+)", search_query)
                if match:
                    landmark = match.group(1)
                    poi_type = match.group(3)
                    coords, formal_name, _ = await ToolService._resolve_coordinates(client, api_key, landmark, city)
                    if coords:
                        anchor_loc = coords
                        search_query = poi_type
                        display_name = f"{formal_name} 附近的 {poi_type}"
                else:
                    coords, formal_name, _ = await ToolService._resolve_coordinates(client, api_key, search_query, city)
                    if coords: anchor_loc = coords

                safe_keyword = urllib.parse.quote(search_query)
                safe_city = urllib.parse.quote(city) if city else "全国"
                center_param = f"&center={anchor_loc}" if anchor_loc else ""

                # 使用常量池中的 URI
                interactive_url = f"{AmapAPI.URI_SEARCH}?keyword={safe_keyword}&city={safe_city}{center_param}&view=map&src=mypage&callnative=0"

                html_widget = f"""
<div style="{UIConstants.WIDGET_STYLE}">
    <div style="{UIConstants.HEADER_STYLE}">
        <div style="font-size: 15px; font-weight: bold; color: #1d1d1f; display: flex; align-items: center; gap: 6px;">
            <span style="font-size: 18px;">📍</span> 周边雷达：{display_name[:15]}...
        </div>
        <a href="{interactive_url}" target="_blank" style="font-size: 12px; color: #ffffff; text-decoration: none; padding: 6px 12px; background: #007aff; border-radius: 20px; font-weight: 500; flex-shrink: 0;">全屏探索</a>
    </div>
    <div style="width: 100%; height: 450px; overflow-y: auto; -webkit-overflow-scrolling: touch; background: #f8f8f8;">
        <iframe src="{interactive_url}" width="100%" height="100%" frameborder="0" allow="geolocation" style="display: block; min-height: 600px;"></iframe>
    </div>
</div>
"""
                text_data = f"已为用户精准定位并展示 【{display_name}】 的交互式搜索结果。"
                return json.dumps({"html_widget": html_widget, "text_data": text_data}, ensure_ascii=False)

            except Exception as e:
                logger.error(f"周边搜索故障: {e}")
                return json.dumps({"text_data": f"周边搜索暂时不可用: {str(e)}"}, ensure_ascii=False)

    @staticmethod
    async def get_weather(city_name: str):
        """🌤️ 沉浸式动态天气卡片"""
        api_key = settings.AMAP_KEY
        if not city_name: return json.dumps({"text_data": "缺少城市名称。"}, ensure_ascii=False)

        async with httpx.AsyncClient() as client:
            try:
                city_name = str(city_name)
                # 使用常量池中的 API
                r_o = await client.get(AmapAPI.GEOCODE, params={"key": api_key, "address": city_name})
                if not r_o.json().get('geocodes'):
                    return json.dumps({"text_data": f"抱歉，无法识别城市：{city_name}"}, ensure_ascii=False)

                city_data = r_o.json()['geocodes'][0]
                adcode = city_data['adcode']
                formatted_city = city_data['city'] if city_data['city'] else city_data['province']

                # 并发请求使用常量 API
                r_live, r_cast = await asyncio.gather(
                    client.get(AmapAPI.WEATHER_INFO, params={"key": api_key, "city": adcode, "extensions": "base"}),
                    client.get(AmapAPI.WEATHER_INFO, params={"key": api_key, "city": adcode, "extensions": "all"})
                )

                data_live = r_live.json()
                data_cast = r_cast.json()

                if data_live.get('status') == '1' and data_live.get('lives'):
                    live = data_live['lives'][0]
                    temp = live['temperature']
                    weather = live['weather']
                    wind_dir = live['winddirection']
                    wind_power = live['windpower']
                    humidity = live['humidity']
                    report_time = live['reporttime'][:10]

                    bg_gradient = "linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)"
                    icon = "☀️"
                    if "雨" in weather:
                        bg_gradient = "linear-gradient(135deg, #616161 0%, #9bc5c3 100%)"
                        icon = "🌧️"
                    elif "云" in weather or "阴" in weather:
                        bg_gradient = "linear-gradient(135deg, #8ba8b5 0%, #b2c6ce 100%)"
                        icon = "☁️"
                    elif "雪" in weather:
                        bg_gradient = "linear-gradient(135deg, #e0eafc 0%, #cfdef3 100%)"
                        icon = "❄️"

                    html_widget = f"""<div style="border-radius: 20px; overflow: hidden; margin: 16px 0; background: {bg_gradient}; box-shadow: 0 10px 30px rgba(0,0,0,0.15); color: #fff; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
<div style="padding: 24px; position: relative;">
<div style="font-size: 22px; font-weight: 600; text-shadow: 0 2px 4px rgba(0,0,0,0.1);">{formatted_city}</div>
<div style="font-size: 64px; font-weight: 300; margin: 10px 0; display: flex; align-items: center; gap: 12px; text-shadow: 0 4px 12px rgba(0,0,0,0.1);">
<span>{temp}°</span> <span style="font-size: 48px;">{icon}</span>
</div>
<div style="font-size: 18px; font-weight: 500; margin-bottom: 20px;">{weather}</div>
<div style="display: flex; justify-content: space-between; background: rgba(255,255,255,0.2); padding: 12px 16px; border-radius: 12px; backdrop-filter: blur(10px); text-shadow: 0 1px 2px rgba(0,0,0,0.1);">
<div style="text-align: center;">
<div style="font-size: 12px; opacity: 0.9; margin-bottom: 4px;">风向</div>
<div style="font-size: 14px; font-weight: bold;">{wind_dir}风 {wind_power}级</div>
</div>
<div style="width: 1px; background: rgba(255,255,255,0.3);"></div>
<div style="text-align: center;">
<div style="font-size: 12px; opacity: 0.9; margin-bottom: 4px;">湿度</div>
<div style="font-size: 14px; font-weight: bold;">{humidity}%</div>
</div>
<div style="width: 1px; background: rgba(255,255,255,0.3);"></div>
<div style="text-align: center;">
<div style="font-size: 12px; opacity: 0.9; margin-bottom: 4px;">更新</div>
<div style="font-size: 14px; font-weight: bold;">{report_time}</div>
</div>
</div>
</div>
"""
                    if data_cast.get('status') == '1' and data_cast.get('forecasts'):
                        casts = data_cast['forecasts'][0]['casts'][1:4]
                        html_widget += """<div style="padding: 16px 24px; background: rgba(0,0,0,0.15); border-top: 1px solid rgba(255,255,255,0.1);">\n"""
                        for day in casts:
                            day_icon = "☀️" if "晴" in day['dayweather'] else "🌧️" if "雨" in day[
                                'dayweather'] else "☁️"
                            html_widget += f"""<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; font-size: 15px;">\n<span style="width: 60px; font-weight:500;">周{day['week']}</span>\n<span style="flex: 1; text-align: center;">{day_icon} {day['dayweather']}</span>\n<span style="width: 80px; text-align: right; opacity: 0.9;">{day['nighttemp']}° / {day['daytemp']}°</span>\n</div>\n"""
                        html_widget += "</div>\n"
                    html_widget += "</div>"

                    text_data = f"已为用户展示精美天气卡片。当前：{weather}，{temp}度。请根据天气（如是否下雨/高温）给出简短贴心的出行、穿衣或洗车建议。"
                    return json.dumps({"html_widget": html_widget, "text_data": text_data}, ensure_ascii=False)
                else:
                    return json.dumps({"text_data": "气象局接口暂无响应。"}, ensure_ascii=False)
            except Exception as e:
                logger.error(f"天气插件故障: {e}")
                return json.dumps({"text_data": f"天气查询暂时不可用: {str(e)}"}, ensure_ascii=False)


# =================================================================
# 🌟 原生 Agent Tool Calling 接口定义
# =================================================================

@tool
async def agent_get_route(origin_name: str, destination_name: str, mode: str = "driving") -> str:
    """
    【必须调用】当用户需要：路线规划、导航、从A地到B地怎么走、查询距离或预计耗时、打车费用。
    参数 mode 可选值: driving (驾车/打车), transit (公交/地铁/火车), walking (步行)。
    """
    return await ToolService.get_route_plan(origin_name, destination_name, mode)


@tool
async def agent_search_nearby(keyword: str, city: str = "全国") -> str:
    """【必须调用】当用户寻找附近、周边的具体设施（如停车场、充电桩、加油站、公共厕所、餐厅、酒店、车站）时调用。"""
    return await ToolService.search_nearby(keyword, city)


@tool
async def agent_get_weather(city_name: str) -> str:
    """当用户询问天气预报、路况天气影响或询问穿衣/洗车等建议时调用。"""
    return await ToolService.get_weather(city_name)