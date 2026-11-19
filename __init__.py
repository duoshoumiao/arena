import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request

from hoshino import Service
from hoshino.typing import *

from .. import chara
from .. import _pcr_data
from .qq_context_requests import *

sv_help = f'''
查询请发送"bjjc/rjjc/tjjc+防守队伍"，无需+号。可以分开发送。
防守队伍可以是五个角色的昵称，也可以是截图。
截图支持局部图片和全局图片，支持多队查询，支持未定队伍查询，支持近似查询。
截图与bjjc/rjjc/tjjc可以分开发送。

【本地模拟】
・竞技场模拟 进攻队 vs 防守队         （也可换行分两行）
・竞技场建议 防守队               （单队，最多给5个解法）
・竞技场建议 1 防守队1⏎防守队2     （多队每队一行，角色不重复）
・克制进攻 进攻1⏎进攻2⏎进攻3       （算最高合格率防守，每3队=1组，支持发图）
・进攻同解 防守1⏎防守2⏎防守3       （3v3通解：3支进攻队无论顺序都赢，支持发图）
・多队时每队单独一行；队内角色空格分隔或直接连写（旧 | 分隔仍可用）
・竞技场状态                       （查本地服务/引擎）

源码：https://github.com/watermellye/arena
'''.strip()

sv = Service('截图拆', help_=sv_help, bundle='pcr查询',enable_on_default=False)
from . import old_main  # 模块加载时导入，确保 @sv.on_fullmatch('竞技场更新卡池') 等指令被注册

# ------------------------------------------------------------------ 本地模拟服务配置
# 「竞技场模拟 / 竞技场建议」调用竞技场 Web 服务。
#
# 默认走公网中转（frp）：
#   本地电脑 8765  -->  124.222.53.156:8765
# 这样 bot 不管跑在哪（本机/云服务器），出门在外都能用同一个地址。
#
# 如果想直连本地（bot 和竞技场服务在同一台机器，延迟更低），设：
#   $env:ARENA_WEB = 'http://127.0.0.1:8765'
ARENA_WEB = os.environ.get(
    'ARENA_WEB', 'http://139.199.14.99:8765').rstrip('/')
ARENA_PASS = os.environ.get('ARENA_PASS', 'pcr2026')
ARENA_TIMEOUT = int(os.environ.get('ARENA_TIMEOUT', '240'))
# 模拟场数（越大越准越慢）
ARENA_SIM_REPEAT = int(os.environ.get('ARENA_SIM_REPEAT', '10'))
# 建议搜索的精度档位：fast(1) / quick(3) / accurate(5) / strict(10)
ARENA_SUGGEST_LEVEL = os.environ.get('ARENA_SUGGEST_LEVEL', 'quick')
# 「稳赢」的胜率默认线（百分比）。胜率 >= 此值才标为稳赢，多队无冲也用它筛。
ARENA_MIN_WIN_RATE = int(os.environ.get('ARENA_MIN_WIN_RATE', '90'))

gs_prefix_all = ('怎么拆', '怎么解', '怎么打', '如何拆', '如何解', '如何打', 'jjc查询')
gs_prefix_bilibili = tuple(["bjjc"] + ['b' + x for x in gs_prefix_all] + ['B' + x for x in gs_prefix_all])
gs_prefix_taiwan = tuple(["tjjc"] + ['t' + x for x in gs_prefix_all] + ['T' + x for x in gs_prefix_all])
gs_prefix_japan = tuple(["rjjc"] + ['r' + x for x in gs_prefix_all] + ['R' + x for x in gs_prefix_all])
gs_prefix_all_time = tuple([x + '按时间' for x in gs_prefix_all] + [x + '时间排序' for x in gs_prefix_all])
gs_prefix_bilibili_time = tuple([x + '按时间' for x in gs_prefix_bilibili] + [x + '时间排序' for x in gs_prefix_bilibili])  
gs_prefix_taiwan_time = tuple([x + '按时间' for x in gs_prefix_taiwan] + [x + '时间排序' for x in gs_prefix_taiwan])  
gs_prefix_japan_time = tuple([x + '按时间' for x in gs_prefix_japan] + [x + '时间排序' for x in gs_prefix_japan])

def IsEmptyMessage(message: Message) -> bool:
    return all([x.type == 'text' and x.data['text'].strip() == '' for x in message])


@sv.on_prefix(gs_prefix_all)
async def QueryAllInterface(bot: HoshinoBot, ev: CQEvent):
    if IsEmptyMessage(ev.message):
        await bot.send(ev, sv.help)
    else:
        await QueryArenaInterface(bot, ev, ev.message, RegionEnum.All)
        await bot.send(ev, f'请使用 bjjc/rjjc/tjjc 以过滤查询的服务器。')

@sv.on_prefix(gs_prefix_all_time)  
async def QueryAllTimeInterface(bot: HoshinoBot, ev: CQEvent):  
    if IsEmptyMessage(ev.message):  
        await bot.send(ev, sv.help)  
    else:  
        await QueryArenaInterface(bot, ev, ev.message, RegionEnum.All, sort_by_time=True)  
        await bot.send(ev, f'请使用 bjjc/rjjc/tjjc 以过滤查询的服务器。')

@sv.on_prefix(gs_prefix_bilibili)
async def QueryBilibiliInterface(bot: HoshinoBot, ev: CQEvent):
    if IsEmptyMessage(ev.message):
        await bot.send(ev, f'已收到查作业（B服）请求，请在 {gs_seconds_to_wait} 秒内发送防守队伍截图')
        gs_qqid2request[ev.user_id] = QueryRequestContext(RegionEnum.Bilibili)
    else:
        await QueryArenaInterface(bot, ev, ev.message, RegionEnum.Bilibili)


@sv.on_prefix(gs_prefix_taiwan)
async def QueryTaiwanInterface(bot: HoshinoBot, ev: CQEvent):
    if IsEmptyMessage(ev.message):
        await bot.send(ev, f'已收到查作业（台服）请求，请在 {gs_seconds_to_wait} 秒内发送防守队伍截图')
        gs_qqid2request[ev.user_id] = QueryRequestContext(RegionEnum.Taiwan)
    else:
        await QueryArenaInterface(bot, ev, ev.message, RegionEnum.Taiwan)


@sv.on_prefix(gs_prefix_japan)
async def QueryJapanInterface(bot: HoshinoBot, ev: CQEvent):
    if IsEmptyMessage(ev.message):
        await bot.send(ev, f'已收到查作业（日服）请求，请在 {gs_seconds_to_wait} 秒内发送防守队伍截图')
        gs_qqid2request[ev.user_id] = QueryRequestContext(RegionEnum.Japan)
    else:
        await QueryArenaInterface(bot, ev, ev.message, RegionEnum.Japan)

@sv.on_prefix(gs_prefix_bilibili_time)  
async def QueryBilibiliTimeInterface(bot: HoshinoBot, ev: CQEvent):  
    if IsEmptyMessage(ev.message):  
        await bot.send(ev, f'已收到查作业（B服，按时间排序）请求，请在 {gs_seconds_to_wait} 秒内发送防守队伍截图')  
        gs_qqid2request[ev.user_id] = QueryRequestContext(RegionEnum.Bilibili, sort_by_time=True)  
    else:  
        await QueryArenaInterface(bot, ev, ev.message, RegionEnum.Bilibili, sort_by_time=True)  
  
  
@sv.on_prefix(gs_prefix_taiwan_time)  
async def QueryTaiwanTimeInterface(bot: HoshinoBot, ev: CQEvent):  
    if IsEmptyMessage(ev.message):  
        await bot.send(ev, f'已收到查作业（台服，按时间排序）请求，请在 {gs_seconds_to_wait} 秒内发送防守队伍截图')  
        gs_qqid2request[ev.user_id] = QueryRequestContext(RegionEnum.Taiwan, sort_by_time=True)  
    else:  
        await QueryArenaInterface(bot, ev, ev.message, RegionEnum.Taiwan, sort_by_time=True)  
  
  
@sv.on_prefix(gs_prefix_japan_time)  
async def QueryJapanTimeInterface(bot: HoshinoBot, ev: CQEvent):  
    if IsEmptyMessage(ev.message):  
        await bot.send(ev, f'已收到查作业（日服，按时间排序）请求，请在 {gs_seconds_to_wait} 秒内发送防守队伍截图')  
        gs_qqid2request[ev.user_id] = QueryRequestContext(RegionEnum.Japan, sort_by_time=True)  
    else:  
        await QueryArenaInterface(bot, ev, ev.message, RegionEnum.Japan, sort_by_time=True)

def GetImageUrlFromMessage(message: Message) -> Optional[str]:
    images = [x.data for x in message if x.type == 'image']
    url = images[0].get("url", None) if len(images) else None
    if url is not None:
        url = url.replace("&amp;", "&").split(",file_size=")[0] # temp
    return url


async def QueryArenaInterface(bot: HoshinoBot, ev: CQEvent, msg: Message, region: RegionEnum, sort_by_time: bool = False):
    image_url = GetImageUrlFromMessage(msg)  
    if image_url:  
        await QueryArenaImageAsync(image_url, region, bot, ev, sort_by_time)  
    else:  
        await QueryArenaTextAsync(msg.extract_plain_text().strip(), region, bot, ev, sort_by_time)
    
@sv.on_message('group')
async def QueryArenaGroupMessageContextInterface(bot: HoshinoBot, ev: CQEvent):
    await QueryArenaMessageContextInterface(bot, ev)

@sv.on_message('private')
async def QueryArenaPrivateMessageContextInterface(bot: HoshinoBot, ev: CQEvent):
    await QueryArenaMessageContextInterface(bot, ev)
    
async def QueryArenaMessageContextInterface(bot: HoshinoBot, ev: CQEvent):
    image_url = GetImageUrlFromMessage(ev.message)
    if not image_url:
        return

    req = PopRequest(ev.user_id)
    if req is None:
        return
    
    await QueryArenaImageAsync(image_url, req.region, bot, ev, req.sort_by_time)

async def QueryArenaImageAsync(image_url: str, region: RegionEnum, bot: HoshinoBot, ev: CQEvent, sort_by_time: bool = False) -> None:
    from .old_main import _QueryArenaImageAsync
    await _QueryArenaImageAsync(image_url, Region2Int(region), bot, ev, sort_by_time)

async def QueryArenaTextAsync(text: str, region: RegionEnum, bot: HoshinoBot, ev: CQEvent, sort_by_time: bool = False) -> None:
    from .old_main import _QueryArenaTextAsync
    await _QueryArenaTextAsync(text, Region2Int(region), bot, ev, sort_by_time)
    
def Region2Int(region: RegionEnum) -> int:
    if region == RegionEnum.All:
        return 1
    if region == RegionEnum.Bilibili:
        return 2
    if region == RegionEnum.Taiwan:
        return 3
    if region == RegionEnum.Japan:
        return 4
    return -1


# ====================================================================
# 本地竞技场模拟（调电脑上的 Web 服务）
# ====================================================================
_token_cache = {'token': None, 'ts': 0}
_token_lock = asyncio.Lock()


def _api_url(path):
    return ARENA_WEB + path


def _http_json(url, payload=None, token=None, timeout=None):
    timeout = timeout or ARENA_TIMEOUT
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        url, data=data, method='POST' if data else 'GET')
    req.add_header('Content-Type', 'application/json; charset=utf-8')
    if token:
        req.add_header('X-Token', token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode('utf-8', 'replace'))
        except Exception:
            return e.code, {'error': 'HTTP %d' % e.code}
    except Exception as e:
        return None, {'error': '%s: %s' % (type(e).__name__, e)}


async def _arena_token(force=False):
    """登录本地 Web 服务拿 token（缓存 6 小时）。"""
    async with _token_lock:
        now = time.time()
        if not force and _token_cache['token'] and now - _token_cache['ts'] < 6 * 3600:
            return _token_cache['token']
        st, j = await asyncio.to_thread(
            _http_json, _api_url('/api/login'), {'password': ARENA_PASS}, None, 20)
        if st == 200 and isinstance(j, dict) and j.get('ok'):
            _token_cache['token'] = j['token']
            _token_cache['ts'] = now
            return j['token']
        return None


async def _arena_post(path, payload, timeout=None):
    tok = await _arena_token()
    if not tok:
        return None, {'error': '连不上本地服务（%s）' % ARENA_WEB}
    st, j = await asyncio.to_thread(
        _http_json, _api_url(path), payload, tok, timeout)
    if st == 401:   # token 过期，重登一次
        tok = await _arena_token(force=True)
        if tok:
            st, j = await asyncio.to_thread(
                _http_json, _api_url(path), payload, tok, timeout)
    return st, j


async def _arena_get(path, timeout=None):
    tok = await _arena_token()
    if not tok:
        return None, {'error': '连不上本地服务'}
    return await asyncio.to_thread(
        _http_json, _api_url(path), None, tok, timeout)


async def _arena_wait_task(tid, deadline_sec, on_tick=None):
    """轮询任务直到结束，返回 (最终快照, 错误)。"""
    since = 0
    end = time.time() + deadline_sec
    last = None
    while time.time() < end:
        await asyncio.sleep(1.5)
        st, s = await _arena_get('/api/task/%s?since=%d' % (tid, since), 30)
        if st != 200 or not isinstance(s, dict):
            continue
        since = s.get('log_count', since)
        last = s
        if on_tick:
            try:
                on_tick(s)
            except Exception:
                pass
        if s.get('state') in ('done', 'error', 'cancelled'):
            return s, None
    return last, '超时（%ds）' % deadline_sec


# ---- 队伍解析：把群里的中文角色名转成 Web 所需的原文 ----
def _parse_team_text(text):
    """用 priconne 的角色库解析队伍，返回 (规范化队伍串, 错误信息)。

    这样群里怎么写都能认（别名、空格、连写），且会校验人数和重复。
    """
    text = (text or '').strip()
    if not text:
        return None, '队伍为空'
    # 先把分隔符统一成空格，避免逗号被当成「不认识的字符」
    for sep in ('，', ',', '、', '|', '｜', '/', '；', ';'):
        text = text.replace(sep, ' ')
    ids, unknown = chara.roster.parse_team(text)
    leftover = unknown.strip()
    if leftover:
        return None, '无法识别的角色名：%s' % leftover
    if len(ids) != 5:
        return None, '需要正好 5 个角色（当前解析出 %d 个）' % len(ids)
    if len(set(ids)) != 5:
        return None, '队伍里有重复角色'
    if any(chara.is_npc(i) for i in ids):
        return None, '队伍里有未实装角色'
    # 转回标准名字串，交给 Web 再解析一遍（Web 有自己的名字表，两者一致）
    names = [chara.fromid(i).name for i in ids]
    return ' '.join(names), None


_TEAM_SPLIT_RE = re.compile(r'\s*(?:vs|VS|Vs|vS|｜|\||\r?\n)\s*')


def _split_defense_teams(text):
    """「竞技场建议 1 队1 | 队2 | 队3」中的队伍拆分。

    多支队伍用换行分隔（推荐写法）；旧的 | ｜ vs 分隔继续兼容。
    每队内部角色用空格隔开，或直接连写（parse_team 会自己切分）。
    支持前缀数字（1/2/3 表示队数，1~3 都接受）：
        竞技场建议 1 防守队伍        -> 单队
        竞技场建议 2 队1 | 队2        -> 两队无冲
        竞技场建议 3 队1 | 队2 | 队3  -> 三队（3v3）
    """
    text = (text or '').strip()
    # 去掉开头的队数声明
    m = re.match(r'^([123])\s*(?:队|个|组)?\s*[:：]?\s*', text)
    declared = None
    if m:
        declared = int(m.group(1))
        text = text[m.end():].strip()

    parts = [p.strip() for p in _TEAM_SPLIT_RE.split(text) if p.strip()]
    return declared, parts


# ------------------------------------------------------------------ 属性图标
# 每个角色头像右上角叠加其「属性」小图标（火/水/风/光/暗）。
# 图标放在本模块 element/ 目录下，映射表 ELEMENT_UNIT_IDS 见下。
from os.path import dirname, join as _join
from PIL import Image as _PILImage

ELEMENT_ICON_SIZE = 20  # 右上角小图标尺寸

# 5 种属性 -> 图标文件名（放在模块目录下的 element/ 里）
ELEMENT_ICON_FILES = {
    "fire":  "fire.png",
    "water": "water.png",
    "wind":  "wind.png",
    "light": "light.png",
    "dark":  "dark.png",
}

# 5 种属性 -> 该属性下的角色 UNIT ID 集合
ELEMENT_UNIT_IDS = {
    "fire":  {1047, 1147, 1095, 1383, 1017, 1177, 1061, 1311, 1065, 1096, 1032, 1136, 1239, 1005, 1090, 1283, 1349, 1066, 1362, 1801, 1004, 1112, 1355, 1267, 1287, 1228, 1165, 1236, 1169, 1071, 1275, 1113, 1302, 1364, 1091, 1030, 1809, 1028, 1081, 1392, 1347, 1178, 1094, 1167, 1297, 1810, 1277, 1126, 1011, 1393, 1278, 1245, 1345, 1099, 1400, 1360, 1285, 1237, 1328, 1323, 1106, 1010, 1272, 1111, 1001, 1168},
    "water": {1270, 1702, 1089, 1125, 1380, 1276, 1056, 1268, 1171, 1104, 1294, 1086, 1131, 1208, 1079, 1075, 1295, 1225, 1117, 1132, 1224, 1115, 1219, 1085, 1199, 1233, 1174, 1229, 1064, 1812, 1279, 1220, 1316, 1170, 1369, 1172, 1116, 1048, 1105, 1389, 1133, 1080, 1173, 1076, 1097, 1226, 1371, 1134, 1388, 1402, 1103, 1325, 1266, 1363, 1269, 1135, 1100, 1293, 1101, 1337, 1322, 1157, 1317, 1403, 1399, 1084, 1077, 1078, 1036, 1227, 1358},
    "wind":  {1052, 1365, 1166, 1162, 1288, 1054, 1021, 1356, 1023, 1037, 1109, 1163, 1238, 1003, 1232, 1394, 1020, 1803, 1128, 1033, 1192, 1053, 1353, 1222, 1127, 1241, 1240, 1263, 1252, 1059, 1130, 1055, 1155, 1301, 1098, 1352, 1026, 1296, 1805, 1308, 1150, 1307, 1262, 1156, 1063, 1107, 1038, 1254, 1025, 1319, 1381, 1129, 1221, 1376, 1015, 1340, 1253, 1312, 1344, 1343, 1040, 1350, 1185, 1042, 1231, 1164, 1251},
    "light": {1701, 1351, 1242, 1321, 1145, 1396, 1058, 1804, 1119, 1029, 1210, 1159, 1249, 1087, 1158, 1144, 1250, 1057, 1118, 1212, 1207, 1049, 1332, 1264, 1143, 1034, 1146, 1248, 1309, 1124, 1395, 1808, 1348, 1397, 1324, 1382, 1346, 1142, 1137, 1138, 1811, 1354, 1068, 1211, 1092, 1160, 1070, 1379, 1374, 1246, 1310, 1016, 1123, 1335, 1121, 1280, 1014, 1122, 1213, 1807, 1265, 1088, 1230, 1329, 1273, 1180, 1012, 1193, 1802, 1289, 1176, 1002, 1284, 1110, 1186},
    "dark":  {1007, 1045, 1191, 1300, 1139, 1331, 1043, 1108, 1141, 1200, 1046, 1359, 1339, 1027, 1330, 1271, 1256, 1298, 1357, 1216, 1299, 1031, 1140, 1401, 1398, 1405, 1260, 1209, 1044, 1361, 1009, 1255, 1336, 1258, 1261, 1387, 1175, 1223, 1051, 1006, 1022, 1257, 1235, 1215, 1082, 1247, 1093, 1069, 1120, 1318, 1190, 1018, 1067, 1182, 1013, 1214, 1806, 1060, 1338, 1050, 1179, 1114, 1391, 1320, 1181, 1290, 1008, 1083, 1390},
}

# 反向索引：UNIT ID -> 属性名
_UNITID_TO_ELEMENT = {}
for _elem, _ids in ELEMENT_UNIT_IDS.items():
    for _uid in _ids:
        _UNITID_TO_ELEMENT[_uid] = _elem

# 预加载属性图标
ELEMENT_ICONS = {}
_curpath = dirname(__file__)
for _elem, _fname in ELEMENT_ICON_FILES.items():
    try:
        _p = _join(_curpath, 'element', _fname)
        ELEMENT_ICONS[_elem] = _PILImage.open(_p).convert('RGBA').resize(
            (ELEMENT_ICON_SIZE, ELEMENT_ICON_SIZE), _PILImage.LANCZOS)
    except Exception as _e:
        sv.logger.warning('pcr-arena 缺少属性图标 element/%s，该属性将不贴图标。' % _fname)


def _get_element_icon(c):
    """按角色对象 c 的 UNIT ID 取属性图标，取不到返回 None"""
    try:
        elem = _UNITID_TO_ELEMENT.get(int(c.id))
    except Exception:
        elem = None
    if elem is None:
        return None
    return ELEMENT_ICONS.get(elem)


# ------------------------------------------------------------------ 图片渲染
async def _render_teams(rows):
    """把若干支队伍画成一张图（用 arena 的角色头像渲染）。

    :param rows: [{'atk': [短id...], 'label': str, 'sub': str}, ...]
                 每行一支队伍，左边 5 个头像，右边显示 label/sub
    :return: PIL Image 或 None
    """
    if not rows:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont

        ICON = 64
        PAD = 4
        GAP = 10
        RIGHT = 170      # 右侧文字区宽度

        # 先把每行解析成角色对象，解析不出 5 人的行直接跳过
        valid = []
        for r in rows:
            chars = []
            for u in (r.get('atk') or []):
                try:
                    chars.append(chara.fromid(int(u)))
                except Exception:
                    continue
            if len(chars) == 5:
                valid.append((r, chars))
        if not valid:
            return None

        W = 5 * ICON + GAP + RIGHT + 8
        H = len(valid) * (ICON + PAD) - PAD

        im = Image.new('RGBA', (W, H), (255, 255, 255, 255))
        draw = ImageDraw.Draw(im)
        try:
            font = ImageFont.truetype('msyh.ttc', 16)
            font_s = ImageFont.truetype('msyh.ttc', 13)
        except Exception:
            font = ImageFont.load_default()
            font_s = font

        for i, (r, chars) in enumerate(valid):
            uids = r.get('atk') or []
            y1 = i * (ICON + PAD)
            for j, c in enumerate(chars):
                x1 = j * ICON
                try:
                    icon = await c.render_icon(ICON)
                except Exception:
                    try:
                        icon = c.render_icon(ICON)
                    except Exception:
                        continue
                try:
                    im.paste(icon, (x1, y1, x1 + ICON, y1 + ICON), icon)
                except Exception:
                    pass
                # 右上角叠加属性图标
                elem_icon = _get_element_icon(c)
                if elem_icon is not None:
                    ex1 = x1 + ICON - ELEMENT_ICON_SIZE
                    ey1 = y1
                    try:
                        im.paste(elem_icon, (ex1, ey1, ex1 + ELEMENT_ICON_SIZE,
                                             ey1 + ELEMENT_ICON_SIZE), elem_icon)
                    except Exception:
                        pass
            # 右侧文字
            tx = 5 * ICON + GAP
            label = str(r.get('label', '') or '')
            sub = str(r.get('sub', '') or '')
            draw.text((tx, y1 + 12), label, (0, 0, 0, 255), font)
            if sub:
                draw.text((tx, y1 + 36), sub, (90, 90, 90, 255), font_s)
        return im
    except Exception as e:
        sv.logger.warning('渲染图片失败：%s' % e)
        return None


def _names_to_short_ids(name_text):
    """把角色名串转成短 id 列表。

    Web 服务返回的是「杏奈，克莉丝提娜，琪爱儿，环奈，环奈(振袖)」这种
    用中文逗号分隔的。parse_team 会把逗号当成「不认识的字符」放进 unknown，
    所以这里先把分隔符统一换成空格，让解析干净。
    """
    if not name_text:
        return []
    text = str(name_text)
    for sep in ('，', ',', '、', '|', '｜', '/', '；', ';'):
        text = text.replace(sep, ' ')
    ids, unknown = chara.roster.parse_team(text)
    # 只剩分隔符残留也算成功（说明角色都认出来了）
    leftover = unknown.strip(' ,、|｜/；;')
    if leftover:
        return []
    return ids


async def _send_teams_image(bot, ev, rows, caption=''):
    """优先发图片，失败则退回文字。返回是否成功发图。"""
    im = await _render_teams(rows)
    if im is None:
        return False
    try:
        from hoshino.util import pic2b64
        msg = str(MessageSegment.image(pic2b64(im)))
        if caption:
            msg = caption + '\n' + msg
        await bot.send(ev, msg)
        return True
    except Exception as e:
        sv.logger.warning('发送图片失败：%s' % e)
        return False


# ---- 指令 1：竞技场模拟 ----
@sv.on_prefix(('竞技场模拟', 'jjc模拟', '模拟对战'), only_to_me=False)
async def arena_simulate(bot: HoshinoBot, ev: CQEvent):
    """竞技场模拟 进攻队 vs 防守队"""
    arg = ev.message.extract_plain_text().strip()
    if not arg:
        await bot.send(ev, sv_help)
        return

    # 用 vs / VS / | / 换行 分隔（进攻一行、防守一行也行）
    parts = [p.strip() for p in _TEAM_SPLIT_RE.split(arg) if p.strip()]
    if len(parts) != 2:
        await bot.send(ev, '格式：竞技场模拟 进攻队 vs 防守队（也可换行分两行）\n'
                           '例：竞技场模拟 环奈 春环 琪爱儿 安涅默涅 雪菲 vs 帆稀 公主佩可 优衣 咲恋 克莉丝提娜')
        return

    atk_txt, err = _parse_team_text(parts[0])
    if err:
        await bot.send(ev, '进攻队：%s' % err)
        return
    def_txt, err = _parse_team_text(parts[1])
    if err:
        await bot.send(ev, '防守队：%s' % err)
        return

    await bot.send(ev, f'⏳ 正在模拟（{ARENA_SIM_REPEAT} 场）…')

    st, j = await _arena_post('/api/simulate', {
        'atk': atk_txt, 'def': def_txt, 'repeat': ARENA_SIM_REPEAT,
    }, ARENA_TIMEOUT)
    if st != 200 or not isinstance(j, dict) or j.get('error'):
        msg = (j or {}).get('error', '请求失败')
        await bot.send(ev, '❌ 模拟失败：%s\n（请确认电脑上「竞技场Web服务」和引擎在运行）' % msg)
        return

    rate = j.get('win_rate', 0) or 0
    emoji = '✅' if rate >= 1.0 else ('⚠️' if rate >= 0.5 else '❌')
    caption = ('%s 胜率 %.0f%%｜%d胜 %d负 %d超时｜均帧 %d\n进攻 vs 防守'
               % (emoji, rate * 100,
                  j.get('win', 0), j.get('lose', 0), j.get('timeout', 0),
                  j.get('avg_frames', 0)))

    # 优先发「图片 + 标题」，失败退回纯文字
    rows = [
        {'atk': _names_to_short_ids(atk_txt), 'label': '进攻', 'sub': 'Attacker'},
        {'atk': _names_to_short_ids(def_txt), 'label': '防守', 'sub': 'Defender'},
    ]
    ok = await _send_teams_image(bot, ev, rows, caption=caption)
    if not ok:
        await bot.send(ev,
                       '%s\n进攻：%s\n防守：%s'
                       % (caption, j.get('atk_names', atk_txt),
                          j.get('def_names', def_txt)))


# ---- 指令 2：竞技场建议（1~3 队，支持文字或截图） ----
async def _teams_from_image(image_url, max_teams=3):
    """用 arena 的识图功能解析截图中的队伍。

    复用 old_main.getBox（就是 bjjc 用的那套识别），返回：
        (队伍列表[[id...], ...], 提示信息)
    """
    from io import BytesIO

    from PIL import Image

    from .old_main import get_pic
    from .old_main import getBox as _get_box

    try:
        raw = await get_pic(image_url)
        img = Image.open(BytesIO(raw))
        box, msg = await _get_box(img)
    except Exception as e:
        return [], '识图失败：%s' % e

    if not box:
        return [], '未识别到有 4 个及以上角色的阵容'

    teams = []
    for team in box:
        ids = [x for x in team if x and x != 1000]
        # 补齐到 5 人（未定位置用 1000 占位，这里跳过）
        if len(ids) < 4:
            continue
        teams.append(ids)
    if not teams:
        return [], '识别到的队伍不足 4 人'
    if len(teams) > max_teams:
        teams = teams[:max_teams]
    return teams, msg


async def _ids_to_team_text(ids):
    """把角色 id 列表转成 Web 能解析的名字串。"""
    names = []
    for i in ids:
        try:
            names.append(chara.fromid(i).name)
        except Exception:
            return None, '角色 id %s 无法识别' % i
    return ' '.join(names), None


@sv.on_prefix(('竞技场建议', 'jjc建议', '建议阵容'), only_to_me=False)
async def arena_suggest(bot: HoshinoBot, ev: CQEvent):
    """竞技场建议 [队数] 防守1 [| 防守2 ...]  或  发图（打完字再发图）"""
    # 先看有没有图片
    image_url = GetImageUrlFromMessage(ev.message)
    arg = ev.message.extract_plain_text().strip()

    # ---------- 情况 A：本消息带图，直接识图 ----------
    if image_url:
        await bot.send(ev, '🖼 正在识别截图中的防守队伍…')
        teams, msg = await _teams_from_image(image_url)
        if not teams:
            await bot.send(ev, '❌ %s' % msg)
            return
        if msg:
            try:
                await bot.send(ev, msg)
            except Exception:
                pass

        # 转成文本队再走统一流程
        def_teams = []
        for i, ids in enumerate(teams, 1):
            t, err = await _ids_to_team_text(ids)
            if err:
                await bot.send(ev, '第 %d 支队伍：%s' % (i, err))
                return
            def_teams.append(t)
        await _do_arena_suggest(bot, ev, def_teams, declared=len(def_teams))
        return

    # ---------- 情况 B：纯文字 ----------
    if not arg:
        await bot.send(ev, '格式（二选一）：\n'
                           '① 打字：竞技场建议 防守队\n'
                           '② 发图：竞技场建议 + 防守队伍截图（可多队）\n\n'
                           '多队无冲（每队一行，队内角色空格分隔或直接连写）：\n'
                           '竞技场建议 2\n'
                           '防守队1\n'
                           '防守队2')

        return

    declared, raw_parts = _split_defense_teams(arg)
    if not raw_parts:
        await bot.send(ev, '没解析到防守队伍')
        return

    def_teams = []
    for i, p in enumerate(raw_parts, 1):
        t, err = _parse_team_text(p)
        if err:
            await bot.send(ev, '第 %d 支防守队：%s' % (i, err))
            return
        def_teams.append(t)

    await _do_arena_suggest(bot, ev, def_teams, declared)


async def _do_arena_suggest(bot, ev, def_teams, declared=None):
    """执行建议阵容搜索（文字和图片共用）。"""
    n = len(def_teams)
    if n > 3:
        await bot.send(ev, '一次最多 3 支防守队')
        return
    if declared and declared != n:
        await bot.send(ev, '声明了 %d 队，实际填了 %d 支，请检查' % (declared, n))
        return
    if n >= 2:
        seen = set()
        for i, t in enumerate(def_teams, 1):
            if t in seen:
                await bot.send(ev, '第 %d 支防守队与前面重复' % i)
                return
            seen.add(t)

    # 单队
    if n == 1:
        await bot.send(ev, f'防守：{def_teams[0]}\n⏳ 正在搜索能打赢的阵容…')
        st, j = await _arena_post('/api/suggest', {
            'def': def_teams[0], 'level': ARENA_SUGGEST_LEVEL,
        }, 30)
        if st != 200 or not isinstance(j, dict) or not j.get('task_id'):
            await bot.send(ev, '❌ 提交失败：%s'
                           % ((j or {}).get('error', '请求失败')))
            return
        snap, err = await _arena_wait_task(j['task_id'], ARENA_TIMEOUT)
        if err or not snap or snap.get('state') != 'done':
            await bot.send(ev, '❌ 搜索失败：%s'
                           % (err or (snap or {}).get('error') or '未知'))
            return
        r = snap.get('result') or {}
        sugg = r.get('suggestions') or []
        if not sugg:
            await bot.send(ev, '⚠️ 没找到能稳赢的阵容')
            return

        # ---- 图片输出：每行一支进攻队，标题带胜率 ----
        rows = []
        caption_lines = ['🎯 防守：%s' % def_teams[0]]
        for i, x in enumerate(sugg[:5]):
            ids = _names_to_short_ids(x.get('atk_names', ''))
            if len(ids) != 5:
                continue
            wr = (x.get('win_rate') or 0) * 100
            solid = wr >= ARENA_MIN_WIN_RATE
            rows.append({
                'atk': ids,
                'label': '%d. %s' % (i + 1, '稳赢' if solid else '建议'),
                'sub': '胜率 %.0f%%  (%d胜%d负)' % (wr, x.get('win', 0), x.get('lose', 0)),
            })
            caption_lines.append('%d. %.0f%%%s（%d胜%d负）'
                                 % (i + 1, wr, ' 稳赢' if solid else '',
                                    x.get('win', 0), x.get('lose', 0)))

        if rows and await _send_teams_image(bot, ev, rows,
                                            caption='\n'.join(caption_lines)):
            return

        # 退路：纯文字
        lines = [f'🎯 防守：{def_teams[0]}', f'找到 {len(sugg)} 个解法：']
        for i, x in enumerate(sugg[:5], 1):
            solid = ' ✓稳赢' if (x.get('win_rate') or 0) * 100 >= ARENA_MIN_WIN_RATE else ''
            lines.append('%d. %s%s\n    胜率 %.0f%%（%d胜%d负）'
                         % (i, x.get('atk_names', ''), solid,
                            (x.get('win_rate') or 0) * 100,
                            x.get('win', 0), x.get('lose', 0)))
        await bot.send(ev, '\n'.join(lines))
        return

    # 多队无冲
    ep = '/api/suggest_3v3' if n == 3 else '/api/suggest_nc'
    mode = '3v3' if n == 3 else '%d 队' % n
    await bot.send(ev, '防守队伍：\n%s\n⏳ 正在搜索无冲突阵容（%s，约 30~90 秒）…'
                   % ('\n'.join('%d. %s' % (i, t) for i, t in enumerate(def_teams, 1)), mode))

    st, j = await _arena_post(ep, {
        'def': '\n'.join(def_teams),
        'level': ARENA_SUGGEST_LEVEL,
        'min_win_rate': ARENA_MIN_WIN_RATE / 100.0,
    }, 30)
    if st != 200 or not isinstance(j, dict) or not j.get('task_id'):
        await bot.send(ev, '❌ 提交失败：%s'
                       % ((j or {}).get('error', '请求失败')))
        return

    snap, err = await _arena_wait_task(j['task_id'], ARENA_TIMEOUT + 90)
    if err or not snap or snap.get('state') != 'done':
        await bot.send(ev, '❌ 搜索失败：%s'
                       % (err or (snap or {}).get('error') or '未知'))
        return

    r = snap.get('result') or {}
    rows = r.get('teams') or []
    if not rows:
        await bot.send(ev, '⚠️ 没找到无冲突解法')
        return

    # ---- 图片输出：每行「防守队 -> 进攻队」 ----
    img_rows = []
    caption_lines = ['🎯 无冲突阵容（%d/%d 队有解）' % (len(rows), n)]
    if r.get('complete') is False:
        caption_lines.append('⚠️ 未能覆盖全部防守队')
    for i, t in enumerate(rows, 1):
        atk_ids = _names_to_short_ids(t.get('atk_names', ''))
        def_ids = _names_to_short_ids(t.get('def_names', ''))
        if len(atk_ids) != 5:
            continue
        wr = (t.get('win_rate') or 0) * 100
        solid = wr >= ARENA_MIN_WIN_RATE
        # 防守队画在上面一行，进攻队画在下面一行，方便对照
        img_rows.append({
            'atk': def_ids if len(def_ids) == 5 else atk_ids,
            'label': '#%d 防守' % i,
            'sub': t.get('def_names', ''),
        })
        img_rows.append({
            'atk': atk_ids,
            'label': '    进攻%s' % (' 稳赢' if solid else ''),
            'sub': '胜率 %.0f%%' % wr,
        })
        caption_lines.append('#%d 防：%s' % (i, t.get('def_names', '')))
        caption_lines.append('    攻：%s  %.0f%%%s'
                             % (t.get('atk_names', ''), wr,
                                ' 稳赢' if solid else ''))

    if img_rows and await _send_teams_image(bot, ev, img_rows,
                                            caption='\n'.join(caption_lines)):
        return

    # 退路：纯文字
    lines = ['🎯 无冲突阵容（%d/%d 队有解）' % (len(rows), n)]
    if r.get('complete') is False:
        lines.append('⚠️ 未能覆盖全部防守队')
    for i, t in enumerate(rows, 1):
        solid = ' ✓稳赢' if (t.get('win_rate') or 0) * 100 >= ARENA_MIN_WIN_RATE else ''
        lines.append('#%d 防：%s\n    攻：%s%s\n    胜率 %.0f%%'
                     % (i, t.get('def_names', ''),
                        t.get('atk_names', ''), solid,
                        (t.get('win_rate') or 0) * 100))
    await bot.send(ev, '\n'.join(lines))


# ---- 指令 3：克制进攻 ----
# 单防守命中率 + 最优无冲突 2 队 / 3 队防守。进攻端无变种：每 3 支进攻队 = 1 组。
ARENA_COUNTER_TIMEOUT = int(os.environ.get('ARENA_COUNTER_TIMEOUT', '900'))
ARENA_COUNTER_CANDIDATES = int(os.environ.get('ARENA_COUNTER_CANDIDATES', '120'))


def _long_ids_to_short(ids):
    """服务端返回的 6 位长 ID → hoshino 短 ID（头像渲染用）。"""
    out = []
    for u in (ids or []):
        try:
            u = int(u)
        except Exception:
            continue
        out.append(u // 100 if u > 99999 else u)
    return out


def _split_teams_arg(text):
    """把「克制进攻 / 通解」的参数拆成若干支队文本。

    分隔符：换行（每队一行，推荐）、| ｜（旧写法仍然兼容）。
    """
    return [p.strip() for p in re.split(r'\s*(?:\||｜|\r?\n)\s*', text)
            if p.strip()]


async def _arena_run_task(bot, ev, ep, body, deadline, busy_hint):
    """提交异步任务并等结果。成功返回 result dict，失败已回复、返回 None。"""
    await bot.send(ev, busy_hint)
    st, j = await _arena_post(ep, body, 30)
    if st != 200 or not isinstance(j, dict) or not j.get('task_id'):
        await bot.send(ev, '❌ 提交失败：%s'
                       % ((j or {}).get('error', '请求失败')))
        return None
    snap, err = await _arena_wait_task(j['task_id'], deadline)
    if err or not snap or snap.get('state') != 'done':
        await bot.send(ev, '❌ 计算失败：%s'
                       % (err or (snap or {}).get('error') or '未知'))
        return None
    return snap.get('result') or {}


@sv.on_prefix(('克制进攻', 'jjc克制', '防守克制'), only_to_me=False)
async def arena_counter(bot: HoshinoBot, ev: CQEvent):
    """克制进攻 [合格线%] 进攻队1 | 进攻队2 | …   或  发截图

    每 3 支进攻队 = 1 组；算「能防住/打超时」的单防守命中率与
    最优无冲突 2 队 / 3 队防守。
    """
    image_url = GetImageUrlFromMessage(ev.message)
    arg = ev.message.extract_plain_text().strip()

    # 可选前缀：合格线百分比，如「克制进攻 80% 队1|队2」
    rate = 1.0
    m = re.match(r'^(\d{1,3})\s*%\s*', arg)
    if m:
        rate = max(0.0, min(1.0, int(m.group(1)) / 100.0))
        arg = arg[m.end():].strip()

    atk_texts = []
    if image_url:
        await bot.send(ev, '🖼 正在识别截图中的进攻队伍…')
        teams, msg = await _teams_from_image(image_url, max_teams=12)
        if not teams:
            await bot.send(ev, '❌ %s' % msg)
            return
        bad = [i for i, ids in enumerate(teams, 1) if len(ids) != 5]
        if bad:
            await bot.send(ev, '❌ 第 %s 支识别到的队伍不足 5 人，'
                               '请截全或改用文字' % '、'.join(map(str, bad)))
            return
        for i, ids in enumerate(teams, 1):
            t, err = await _ids_to_team_text(ids)
            if err:
                await bot.send(ev, '第 %d 支队伍：%s' % (i, err))
                return
            atk_texts.append(t)
    if not atk_texts:
        if not arg:
            await bot.send(ev, '格式（二选一）：\n'
                               '① 克制进攻 + 进攻队（每队一行，队内角色空格分隔或直接连写）\n'
                               '② 克制进攻 + 进攻队伍截图（可多队）\n\n'
                               '每 3 支队 = 1 组进攻；可选前缀合格线，如：\n'
                               '克制进攻 80% 队1⏎队2⏎队3')
            return
        for i, p in enumerate(_split_teams_arg(arg), 1):
            t, err = _parse_team_text(p)
            if err:
                await bot.send(ev, '第 %d 支进攻队：%s' % (i, err))
                return
            atk_texts.append(t)

    n = len(atk_texts)
    group_count = (n + 2) // 3
    r = await _arena_run_task(
        bot, ev, '/api/counter',
        {'atk_groups': '\n'.join(atk_texts),
         'min_qualified_rate': rate,
         'sim_per_pair': 1,
         'candidate_limit': ARENA_COUNTER_CANDIDATES,
         'save_hot': True},
        ARENA_COUNTER_TIMEOUT,
        '⏳ 克制进攻计算中（%d 支进攻队 / %d 组，合格线 %.0f%%，'
        '约需 1~10 分钟，请稍候）…' % (n, group_count, rate * 100))
    if r is None:
        return
    await _send_counter_result(bot, ev, r, atk_texts)


async def _send_counter_result(bot, ev, r, atk_texts):
    """克制进攻结果：优先图文，失败退回纯文字。"""
    total = r.get('total_attacks') or 0
    best3 = r.get('best3') or {}
    best2 = r.get('best2') or {}
    ok = bool(r.get('qualified'))
    need_n = r.get('qualified_team_count') or 2
    head = ('%s 克制进攻：3 队组合命中率 %.1f%%（守住 %d/%d 个进攻队）'
            % ('🎯 达标' if ok else '⚠️ 未达标',
               (r.get('qualified_rate') or 0) * 100,
               best3.get('covered_count') or 0, total))
    sub = ('单独达标 %d 支（需 ≥%d 支）｜合格线 %.0f%%｜用时 %.0f 秒 / 模拟 %d 场'
           % (r.get('qualified_count') or 0, need_n,
              (r.get('min_qualified_rate') or 0) * 100,
              r.get('elapsed') or 0, r.get('sim_count') or 0))
    if r.get('conflict'):
        sub += '\n⚠️ ' + r['conflict']

    # ---- 图片：3 队防守 + 2 队防守 + 单防守 TOP ----
    rows = []
    caption = [head, sub]
    for title, combo in (('3队防守', best3), ('2队防守', best2)):
        for i, d in enumerate(combo.get('def_team') or [], 1):
            ids = _long_ids_to_short(d.get('def_ids'))
            if len(ids) != 5:
                continue
            q = '达标' if d.get('meets_min') else '补位'
            rows.append({
                'atk': ids,
                'label': '%s#%d' % (title, i),
                'sub': '守住 %d/%d（%.0f%%）%s' % (
                    d.get('held_count') or 0, total,
                    (d.get('hold_rate') or 0) * 100, q),
            })
    for i, s in enumerate((r.get('single_defenses') or [])[:5], 1):
        ids = _long_ids_to_short(s.get('def_ids'))
        if len(ids) != 5:
            continue
        rows.append({
            'atk': ids,
            'label': '单防#%d' % i,
            'sub': '命中率 %.0f%%（%d/%d）' % (
                (s.get('hold_rate') or 0) * 100,
                s.get('held_count') or 0, s.get('total_attacks') or total),
        })

    if rows:
        sent = await _send_teams_image(bot, ev, rows,
                                       caption='\n'.join(caption))
        if sent:
            return

    # ---- 退路：纯文字 ----
    lines = list(caption)
    for title, combo in (('🛡 最优 3 队防守', best3), ('🛡 最优 2 队防守', best2)):
        dts = combo.get('def_team') or []
        if not dts:
            continue
        lines.append('%s（组合命中率 %.1f%%）'
                     % (title, (combo.get('qualified_rate') or 0) * 100))
        for i, d in enumerate(dts, 1):
            q = '✓达标' if d.get('meets_min') else '补位'
            lines.append('  %d. %s — 单独守住 %d/%d（%.0f%%）%s'
                         % (i, d.get('def_names', ''),
                            d.get('held_count') or 0, total,
                            (d.get('hold_rate') or 0) * 100, q))
    singles = r.get('single_defenses') or []
    if singles:
        lines.append('📊 单防守命中率 TOP-%d' % min(len(singles), 5))
        for i, s in enumerate(singles[:5], 1):
            lines.append('  %d. %s — %.0f%%（%d/%d）'
                         % (i, s.get('def_names', ''),
                            (s.get('hold_rate') or 0) * 100,
                            s.get('held_count') or 0,
                            s.get('total_attacks') or total))
    if not rows and len(lines) == len(caption):
        lines.append('没找到能守住任何进攻队的防守队')
    await bot.send(ev, '\n'.join(lines))


# ---- 指令 4：进攻通解（3v3）----
ARENA_UNIVERSAL_TIMEOUT = int(os.environ.get('ARENA_UNIVERSAL_TIMEOUT', '900'))
ARENA_UNIVERSAL_CANDIDATES = int(os.environ.get('ARENA_UNIVERSAL_CANDIDATES', '120'))


@sv.on_prefix(('进攻同解', 'jjc同解', '竞技场通解', 'jjc通解', '进攻通解'), only_to_me=False)
async def arena_universal(bot: HoshinoBot, ev: CQEvent):
    """进攻同解 [共同N] 防守队1 | 防守队2 | 防守队3   或  发截图

    给定对方 3 支防守队，找我方 3 支无冲突进攻队，
    使它们【共同】打赢 ≥N 支防守队（这几支无论对方怎么排顺序都会被赢）。
    """
    image_url = GetImageUrlFromMessage(ev.message)
    arg = ev.message.extract_plain_text().strip()

    # 可选前缀：「共同2」/「需共同2」，默认 2
    shared = 2
    m = re.match(r'^(?:需)?共同\s*([123])\s*', arg)
    if m:
        shared = int(m.group(1))
        arg = arg[m.end():].strip()

    def_texts = []
    if image_url:
        await bot.send(ev, '🖼 正在识别截图中的防守队伍…')
        teams, msg = await _teams_from_image(image_url, max_teams=3)
        if not teams:
            await bot.send(ev, '❌ %s' % msg)
            return
        if len(teams) != 3:
            await bot.send(ev, '❌ 进攻通解需要恰好 3 支防守队（识别到 %d 支）'
                           % len(teams))
            return
        for i, ids in enumerate(teams, 1):
            if len(ids) != 5:
                await bot.send(ev, '❌ 第 %d 支识别到的队伍不足 5 人，请截全' % i)
                return
            t, err = await _ids_to_team_text(ids)
            if err:
                await bot.send(ev, '第 %d 支队伍：%s' % (i, err))
                return
            def_texts.append(t)
    if not def_texts:
        if not arg:
            await bot.send(ev, '格式（二选一）：\n'
                               '① 进攻同解 + 三支防守队（每队一行，队内角色空格分隔或直接连写）\n'
                               '② 进攻同解 + 对方防守截图（3 队）\n\n'
                               '可选前缀「共同N」指定需共同打赢几支（1~3，默认 2）')
            return
        parts = _split_teams_arg(arg)
        if len(parts) != 3:
            await bot.send(ev, '❌ 需要恰好 3 支防守队（当前 %d 支），每队单独一行'
                           % len(parts))
            return
        for i, p in enumerate(parts, 1):
            t, err = _parse_team_text(p)
            if err:
                await bot.send(ev, '第 %d 支防守队：%s' % (i, err))
                return
            def_texts.append(t)

    r = await _arena_run_task(
        bot, ev, '/api/attack_universal',
        {'def_lineup': '\n'.join(def_texts),
         'min_shared_def': shared,
         'sim_per_pair': 1,
         'candidate_limit': ARENA_UNIVERSAL_CANDIDATES},
        ARENA_UNIVERSAL_TIMEOUT,
        '⏳ 进攻通解计算中（对方 3 支防守，需共同打赢 ≥%d 支，'
        '约需 1~10 分钟，请稍候）…' % shared)
    if r is None:
        return
    await _send_universal_result(bot, ev, r, def_texts)


async def _send_universal_result(bot, ev, r, def_texts):
    """进攻通解结果：优先图文，失败退回纯文字。"""
    ok = bool(r.get('universal'))
    shared = r.get('min_shared_def') or 2
    best = r.get('best3') or {}
    sh = best.get('shared_defenses') or []
    perms = best.get('permutations') or []
    perm_pass = sum(1 for p in perms if p.get('pass'))

    head = ('%s 进攻通解：我方 3 支进攻队共同打赢 %d/3 支对方防守队（需 ≥%d）'
            % ('🎯 通解成立' if ok else '⚠️ 未找到通解',
               best.get('shared_def_count') or 0, shared))
    if best.get('no_conflict'):
        sub = '无冲突'
    else:
        sub = '⚠️ %s' % (best.get('conflict') or '进攻队之间有角色重复')
    if perms:
        sub += '｜%d 种出场顺序 %d 通过' % (len(perms), perm_pass)
    sub += '｜用时 %.0f 秒 / 模拟 %d 场' % (r.get('elapsed') or 0,
                                            r.get('sim_count') or 0)

    # ---- 图片：我方 3 支进攻队 + 共同打赢的防守队 ----
    rows = []
    caption = [head, sub.strip('｜ ')]
    for i, t in enumerate(best.get('attack_teams') or [], 1):
        ids = _long_ids_to_short(t.get('def_ids'))
        if len(ids) != 5:
            continue
        rows.append({
            'atk': ids,
            'label': '我方进攻#%d' % i,
            'sub': '打赢对方 %d/3 支' % (t.get('win_count') or 0),
        })
    for d in sh:
        ids = _long_ids_to_short(d.get('ids'))
        if len(ids) != 5:
            continue
        rows.append({
            'atk': ids,
            'label': '共同打赢★',
            'sub': d.get('names', ''),
        })

    if rows and ok:
        sent = await _send_teams_image(bot, ev, rows,
                                       caption='\n'.join(caption))
        if sent:
            return

    # ---- 退路：纯文字 ----
    lines = list(caption)
    atk_teams = best.get('attack_teams') or []
    if atk_teams:
        lines.append('🗡 我方 3 支进攻队（互不冲突）：')
        for i, t in enumerate(atk_teams, 1):
            lines.append('  第%d队：%s — 打赢对方 %d/3 支'
                         % (i, t.get('def_names', ''),
                            t.get('win_count') or 0))
    if sh:
        lines.append('✅ 共同打赢（无论出场顺序）：')
        for d in sh:
            lines.append('  %s' % d.get('names', ''))
    if not ok:
        lines.append('扩展 %s 轮后仍未找到通解，可稍后再试或降低「共同N」'
                     % (r.get('expand_rounds') or 0))
        if r.get('stop_reason'):
            lines.append('⏹ %s' % r['stop_reason'])
    await bot.send(ev, '\n'.join(lines))


# ---- 指令 5：状态检查 ----
@sv.on_fullmatch(('竞技场状态', '竞技场status'))
async def arena_status(bot: HoshinoBot, ev: CQEvent):
    tok = await _arena_token(force=True)
    if not tok:
        await bot.send(ev, '❌ 连不上本地竞技场服务\n地址：%s\n'
                            '请确认电脑上「竞技场Web服务.exe」在运行' % ARENA_WEB)
        return
    await bot.send(ev, '✅ Web 服务在线（%s）\n正在检查引擎…' % ARENA_WEB)
    st, j = await _arena_post('/api/simulate', {
        'atk': '环奈 春环 琪爱儿 安涅默涅 雪菲',
        'def': '帆稀 公主佩可 优衣 咲恋 克莉丝提娜',
        'repeat': 1,
    }, 120)
    if st == 200 and isinstance(j, dict) and 'error' not in j:
        await bot.send(ev, '✅ 引擎正常（单场模拟成功，胜率 %.0f%%）'
                       % ((j.get('win_rate') or 0) * 100))
    else:
        await bot.send(ev, '⚠️ 引擎异常：%s'
                       % ((j or {}).get('error', '未知错误')))