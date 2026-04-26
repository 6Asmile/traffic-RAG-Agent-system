# app/services/tool_service.py

import httpx
import logging
from app.core.config import settings
from langchain_core.tools import tool
import urllib.parse
logger = logging.getLogger(__name__)


class ToolService:

    @staticmethod
    async def _resolve_coordinates(client, api_key, address, city=None):
        """
        [核心优化] 智能坐标解析助手
        策略：先用【POI关键字搜索】(模糊匹配强)，如果失败再用【地理编码】(精确地址强)
        返回：(经纬度字符串, 标准地点名称, 城市编码)
        """
        # 策略1：尝试 POI 搜索 (适合 "桂电"、"万达广场" 这种模糊地名)
        poi_url = "https://restapi.amap.com/v3/place/text"
        res = await client.get(poi_url, params={
            "key": api_key,
            "keywords": address,
            "city": city,
            "offset": 1,
            "extensions": "all"
        })
        data = res.json()
        if data['status'] == '1' and data['pois']:
            top_poi = data['pois'][0]
            return top_poi['location'], top_poi['name'], top_poi['adcode']

        # 策略2：尝试地理编码 (适合 "xx市xx路xx号" 这种结构化地址)
        geo_url = "https://restapi.amap.com/v3/geocode/geo"
        res = await client.get(geo_url, params={"key": api_key, "address": address})
        data = res.json()
        if data['status'] == '1' and data['geocodes']:
            geo = data['geocodes'][0]
            return geo['location'], geo['formatted_address'], geo['adcode']

        return None, None, None

    @staticmethod
    async def get_route_plan(origin_name: str, destination_name: str, mode: str = "driving"):
        api_key = settings.AMAP_KEY
        if not api_key: return "❌ 未配置地图 API Key"

        async with httpx.AsyncClient() as client:
            try:
                # 1. 智能解析
                o_loc, o_name, o_adcode = await ToolService._resolve_coordinates(client, api_key, origin_name)
                d_loc, d_name, _ = await ToolService._resolve_coordinates(client, api_key, destination_name)

                if not o_loc or not d_loc:
                    return f"🤔 抱歉，无法识别地点 **{origin_name if not o_loc else destination_name}**，请尝试输入更具体的名称。"

                # 2. 【核心修复】URL 编码，防止乱码
                # 高德静态地图要求标记格式：markers=mid,颜色,标签:经纬度
                # 标签部分的中文必须编码
                safe_o_name = urllib.parse.quote(o_name[:5]) # 截取前5字防止过长
                safe_d_name = urllib.parse.quote(d_name[:5])
                static_map_url = f"https://restapi.amap.com/v3/staticmap?key={api_key}&size=750*400&markers=mid,0xFF0000,{safe_o_name}:{o_loc}|mid,0x008000,{safe_d_name}:{d_loc}"

                res_msg = f"![路线预览]({static_map_url})\n\n"
                res_msg += f"🚩 **起点**：{o_name}\n🏁 **终点**：{d_name}\n\n"

                # 3. 模式匹配逻辑优化
                if mode in ["transit", "train", "plane"]: # 增加对火车飞机的兼容（虽然高德API统称transit）
                    mode = "transit"

                # 4. 根据模式请求 API
                if mode == "driving":
                    url = "https://restapi.amap.com/v3/direction/driving"
                    # strategy=10: 躲避拥堵且不走高速等综合策略
                    resp = await client.get(url, params={
                        "key": api_key,
                        "origin": o_loc,
                        "destination": d_loc,
                        "strategy": 10,
                        "extensions": "all"
                    })
                    data = resp.json()

                    if data['status'] == '1' and data['route']['paths']:
                        route = data['route']['paths'][0]
                        distance_km = round(int(route['distance']) / 1000, 2)
                        duration_min = round(int(route['duration']) / 60)
                        traffic_lights = route.get('traffic_lights', 0)
                        toll_distance = route.get('toll_distance', 0)
                        taxi_cost = data['route'].get('taxi_cost', '未知')

                        res_msg += f"🚗 **智能驾车方案**\n"
                        res_msg += f"━━━━━━━━━━━━━━━━━━\n"
                        res_msg += f"- 📏 **总里程**：{distance_km} 公里\n"
                        res_msg += f"- ⏱️ **预计耗时**：{duration_min} 分钟\n"
                        res_msg += f"- 🚦 **红绿灯**：{traffic_lights} 个\n"
                        if int(toll_distance) > 0:
                            res_msg += f"- 🛣️ **高速里程**：{round(int(toll_distance) / 1000, 1)} 公里 (预计过路费 {route.get('tolls', 0)} 元)\n"
                        res_msg += f"- 🚕 **打车约**：{taxi_cost} 元\n\n"

                        res_msg += "**【关键路段指引】**\n"
                        steps = route['steps']
                        for i, step in enumerate(steps):
                            action = step.get('action', '')
                            icon = "⬆️"
                            if '左' in action:
                                icon = "⬅️"
                            elif '右' in action:
                                icon = "➡️"
                            elif '调头' in action:
                                icon = "↩️"

                            road_name = step.get('road', '无名道路')
                            dist = int(step.get('distance', 0))

                            # 只显示大于200米的路段或前/后关键步骤
                            if dist > 200 or i < 2 or i > len(steps) - 3:
                                if not road_name: road_name = step.get('instruction', '').split('行驶')[0]
                                res_msg += f"{icon} 沿 **{road_name}** 行驶 {round(dist / 1000, 1)}公里\n"

                        res_msg += "🏁 到达目的地\n"
                    else:
                        res_msg += "⚠️ 未找到驾车路线，距离可能过远或无法通车。"

                elif mode == "transit":
                    url = "https://restapi.amap.com/v3/direction/transit/integrated"
                    resp = await client.get(url, params={
                        "key": api_key,
                        "origin": o_loc,
                        "destination": d_loc,
                        "city": o_adcode,
                        "strategy": 0
                    })
                    data = resp.json()

                    if data['status'] == '1' and data['route']['transits']:
                        plan = data['route']['transits'][0]
                        total_walk = plan.get('walking_distance', 0)
                        cost = plan.get('cost', '未知')
                        duration = round(int(plan['duration']) / 60)

                        res_msg += f"🚌 **公交/地铁推荐方案**\n"
                        res_msg += f"━━━━━━━━━━━━━━━━━━\n"
                        res_msg += f"- ⏱️ **预计耗时**：{duration} 分钟\n"
                        res_msg += f"- 💰 **票价**：{cost} 元\n"
                        res_msg += f"- 🚶 **步行总计**：{total_walk} 米\n\n"

                        res_msg += "**【详细换乘】**\n"
                        segments = plan.get('segments', [])
                        for seg in segments:
                            if seg.get('walking') and int(seg['walking']['distance']) > 0:
                                res_msg += f"🚶 步行 {seg['walking']['distance']}米\n"

                            if seg.get('bus') and seg['bus']['buslines']:
                                line = seg['bus']['buslines'][0]
                                start_stop = line['departure_stop']['name']
                                end_stop = line['arrival_stop']['name']
                                type_name = line.get('type', '公交')

                                icon = "🚇" if "地铁" in type_name or "轨道" in type_name else "🚌"
                                res_msg += f"{icon} 在 **{start_stop}** 乘坐 **{line['name']}**\n"
                                res_msg += f"   (`经过 {line['via_num']} 站`)\n"
                                res_msg += f"   🔻 在 **{end_stop}** 下车\n"
                        res_msg += "🏁 到达终点\n"
                    else:
                        res_msg += "⚠️ 未找到合适的公交方案（可能跨城或距离过近）。"

                elif mode == "walking":
                    url = "https://restapi.amap.com/v3/direction/walking"
                    resp = await client.get(url, params={"key": api_key, "origin": o_loc, "destination": d_loc})
                    data = resp.json()
                    if data['status'] == '1' and data['route']['paths']:
                        path = data['route']['paths'][0]
                        res_msg += f"🚶 **步行导航**\n"
                        res_msg += f"- 📏 距离：{path['distance']} 米\n"
                        res_msg += f"- ⏱️ 耗时：{round(int(path['duration']) / 60)} 分钟\n\n"
                        for step in path['steps']:
                            res_msg += f"• {step['instruction']}\n"

                res_msg += f"\n*温馨提示：数据由高德地图实时提供，请注意出行安全。*"
                return res_msg

            except Exception as e:
                logger.error(f"地图插件报错: {e}")
                return f"调用路线规划出错: {str(e)}"

    @staticmethod
    async def search_nearby(keyword: str, city: str = "全国"):
        """高德 POI 关键字搜索"""
        api_key = settings.AMAP_KEY
        async with httpx.AsyncClient() as client:
            url = "https://restapi.amap.com/v3/place/text"
            resp = await client.get(url, params={
                "key": api_key,
                "keywords": keyword,
                "city": city,
                "city_limit": "true",
                "offset": 5,
                "extensions": "all"
            })
            data = resp.json()

            if data['status'] == '1' and data['pois']:
                res = f"📍 已为您找到 **{city}** 附近的 **【{keyword}】**：\n\n"
                for i, poi in enumerate(data['pois']):
                    rating = poi.get('biz_ext', {}).get('rating', '')
                    rating_str = f" ⭐{rating}" if rating else ""

                    res += f"**{i + 1}. {poi['name']}**{rating_str}\n"
                    res += f"   📍 地址：`{poi['address']}`\n"
                    if poi.get('tel'):
                        res += f"   📞 电话：{poi['tel']}\n"
                    if poi.get('type'):
                        res += f"   🏷️ 类型：{poi['type'].split(';')[-1]}\n"
                    res += "\n"
                return res
            return f"抱歉，在 {city} 没有搜到相关的【{keyword}】信息。"

    @staticmethod
    async def get_weather(city_name: str):
        """高德天气查询 (包含实时 + 未来预报)"""
        api_key = settings.AMAP_KEY
        async with httpx.AsyncClient() as client:
            # 1. 获取 adcode
            geo_url = "https://restapi.amap.com/v3/geocode/geo"
            r_o = await client.get(geo_url, params={"key": api_key, "address": city_name})

            if not r_o.json().get('geocodes'):
                return f"无法识别城市：{city_name}"

            city_data = r_o.json()['geocodes'][0]
            adcode = city_data['adcode']
            formatted_city = city_data['city'] if city_data['city'] else city_data['province']

            # 2. 获取实时天气与预报
            weather_url = "https://restapi.amap.com/v3/weather/weatherInfo"
            r_live = await client.get(weather_url, params={"key": api_key, "city": adcode, "extensions": "base"})
            r_cast = await client.get(weather_url, params={"key": api_key, "city": adcode, "extensions": "all"})

            msg = f"🌤️ **{formatted_city} 天气播报**\n━━━━━━━━━━━━━━━━━━\n"

            data_live = r_live.json()
            if data_live['status'] == '1' and data_live['lives']:
                live = data_live['lives'][0]
                msg += f"**【当前实况】**\n"
                msg += f"🌡️ 温度：**{live['temperature']}℃**\n"
                msg += f"☁️ 天气：{live['weather']}\n"
                msg += f"🌬️ 风力：{live['winddirection']}风 {live['windpower']}级\n"
                msg += f"💧 湿度：{live['humidity']}%\n\n"

            data_cast = r_cast.json()
            if data_cast['status'] == '1' and data_cast['forecasts']:
                casts = data_cast['forecasts'][0]['casts']
                msg += f"**【未来预报】**\n"
                for day in casts[1:]:
                    date_str = day['date'][5:]
                    icon_day = "☀️" if "晴" in day['dayweather'] else "🌧️" if "雨" in day['dayweather'] else "☁️"
                    msg += f"📅 `{date_str}` 周{day['week']}：{icon_day} {day['dayweather']} ({day['nighttemp']}~{day['daytemp']}℃)\n"

            return msg


# =================================================================
# 🌟 Tool Docstrings
# =================================================================

@tool
async def agent_get_route(origin_name: str, destination_name: str, mode: str = "driving") -> str:
    """
    【核心工具】当用户询问：
    1. 路线规划、怎么走、导航、距离多远、打车多少钱。
    2. 交通方式，如：坐火车、坐飞机、坐高铁、坐地铁、坐公交、开车、步行。

    参数 mode 可选值:
    - driving: 驾车、打车、私家车。
    - transit: 公交、地铁、火车、高铁、长途客车、飞机。
    - walking: 步行、走路、骑行。
    """
    return await ToolService.get_route_plan(origin_name, destination_name, mode)


@tool
async def agent_search_nearby(keyword: str, city: str = "全国") -> str:
    """
    【核心工具】当用户寻找具体的地点、设施或机构信息时调用。
    例如：
    - "附近的加油站"、"找个充电桩"、"最近的交警大队"。
    - "哪里有修车厂"、"查询车管所地址"。
    - "这个地方在哪"、"查询某某大厦"。
    """
    return await ToolService.search_nearby(keyword, city)


@tool
async def agent_get_weather(city_name: str) -> str:
    """
    【核心工具】当用户询问天气、气温、下雨、是否适合洗车、路况天气影响时调用。
    """
    return await ToolService.get_weather(city_name)