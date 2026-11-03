"""QQ 内置表情目录：让奥米按情绪挑一个合适的表情，而不是乱发表情包。"""

from __future__ import annotations

import re
from dataclasses import dataclass

FACE_RE = re.compile(r"\[\[?face:(\d{1,3})\]\]?")


@dataclass(frozen=True, slots=True)
class FaceSticker:
    id: str
    name: str
    mood: str
    keywords: tuple[str, ...]

    @property
    def marker(self) -> str:
        return f"[[face:{self.id}]]"

    def summary(self) -> str:
        return f"{self.marker} {self.name}（{self.mood}）"


# QQ 经典表情 ID 在 NapCat/OneBot 中通用；这里只放常用、语义稳定的。
CATALOG: tuple[FaceSticker, ...] = (
    FaceSticker("14", "微笑", "礼貌/开心", ("微笑", "开心", "笑", "hello", "嗨")),
    FaceSticker("13", "呲牙", "傻乐/得意", ("呲牙", "得意", "开心", "哈哈", "笑")),
    FaceSticker("4", "得意", "小得意", ("得意", "骄傲", "厉害", "夸我")),
    FaceSticker("12", "调皮", "调皮/逗人", ("调皮", "逗", "坏", "嘿嘿")),
    FaceSticker("21", "可爱", "卖萌/撒娇", ("可爱", "卖萌", "撒娇", "黏人")),
    FaceSticker("20", "偷笑", "偷笑/暗爽", ("偷笑", "暗爽", "坏笑", "嘿嘿")),
    FaceSticker("6", "害羞", "害羞/被夸", ("害羞", "脸红", "被夸", "不好意思")),
    FaceSticker("5", "流泪", "感动/委屈", ("流泪", "感动", "哭", "委屈")),
    FaceSticker("9", "大哭", "难过/破防", ("大哭", "难过", "破防", "伤心")),
    FaceSticker("49", "委屈", "吃醋/委屈", ("委屈", "吃醋", "不高兴", "哼")),
    FaceSticker("10", "尴尬", "尴尬/无语", ("尴尬", "无语", "汗", "呃")),
    FaceSticker("32", "疑问", "疑惑/没懂", ("疑问", "疑惑", "问号", "不懂", "啊")),
    FaceSticker("28", "憨笑", "憨憨/放松", ("憨笑", "憨", "傻", "放松")),
    FaceSticker("18", "抓狂", "抓狂/崩溃", ("抓狂", "崩溃", "烦", "救命")),
    FaceSticker("25", "困", "困/想睡", ("困", "睡觉", "晚安", "疲惫")),
    FaceSticker("27", "流汗", "紧张/汗", ("流汗", "紧张", "汗", "压力")),
    FaceSticker("30", "奋斗", "加油/努力", ("奋斗", "加油", "努力", "学习")),
    FaceSticker("36", "衰", "倒霉/无奈", ("衰", "倒霉", "无奈", "惨")),
    FaceSticker("41", "抠鼻", "嫌弃/淡定", ("抠鼻", "嫌弃", "淡定", "无所谓")),
    FaceSticker("42", "鼓掌", "鼓掌/捧场", ("鼓掌", "厉害", "捧场", "好耶")),
    FaceSticker("44", "坏笑", "坏笑/使坏", ("坏笑", "使坏", "计划通", "嘿嘿")),
    FaceSticker("47", "哈欠", "无聊/困", ("哈欠", "无聊", "困", "没劲")),
    FaceSticker("52", "亲亲", "亲亲/黏人", ("亲亲", "亲", "黏人", "爱你")),
    FaceSticker("63", "玫瑰", "送花/浪漫", ("玫瑰", "花", "浪漫", "喜欢")),
    FaceSticker("66", "爱心", "喜欢/比心", ("爱心", "比心", "喜欢", "爱你")),
    FaceSticker("74", "便便", "吐槽/无语", ("便便", "吐槽", "无语", "烂")),
    FaceSticker("75", "月亮", "晚安/夜晚", ("月亮", "晚安", "晚上", "睡觉")),
    FaceSticker("76", "太阳", "早安/阳光", ("太阳", "早安", "早上", "阳光")),
    FaceSticker("79", "强", "点赞/夸", ("强", "赞", "厉害", "牛")),
    FaceSticker("80", "弱", "菜/不行", ("弱", "菜", "不行", "输了")),
    FaceSticker("83", "抱拳", "拜托/感谢", ("抱拳", "拜托", "感谢", "谢谢")),
    FaceSticker("87", "爱你", "爱你/撒娇", ("爱你", "喜欢", "撒娇", "亲")),
    FaceSticker("111", "哈哈", "大笑", ("哈哈", "大笑", "笑死", "开心")),
)

BY_ID = {item.id: item for item in CATALOG}


def search(query: str, limit: int = 6) -> list[FaceSticker]:
    text = (query or "").strip().lower()
    if not text:
        return list(CATALOG[:limit])
    scored: list[tuple[int, FaceSticker]] = []
    for item in CATALOG:
        haystack = f"{item.name} {item.mood} {' '.join(item.keywords)}".lower()
        score = 0
        if text in haystack:
            score += 5
        for part in re.split(r"[\s,，。.!！?？:：;；]+", text):
            if len(part) >= 1 and part in haystack:
                score += 2
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]


def prompt_catalog(limit: int = 24) -> str:
    items = CATALOG[:limit]
    return "\n".join(f"- {item.marker} {item.name}：{item.mood}" for item in items)


def face_ids(text: str) -> list[str]:
    return FACE_RE.findall(text or "")
