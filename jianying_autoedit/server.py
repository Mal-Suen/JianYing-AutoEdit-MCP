# -*- coding: utf-8 -*-
"""JianYing-AutoEdit-MCP 服务器入口：FastMCP 工具层（薄封装，逻辑在 core.DraftRegistry）。

stdio 默认；HTTP 传输（streamable-http）规划中。
"""

from typing import Optional, Union

from mcp.server.fastmcp import FastMCP

from .core import DraftRegistry, TimeSpec

mcp = FastMCP("JianYingAutoEdit")
registry = DraftRegistry()


@mcp.tool()
def create_draft(draft_name: str, width: int = 1920, height: int = 1080,
                 fps: int = 30, allow_replace: bool = False) -> dict:
    """创建剪映草稿并立即落盘注册（剪映刷新后可见）。

    Args:
        draft_name: 草稿名（即剪映里显示的名称，人类可读）
        width: 视频宽度（像素），默认 1920
        height: 视频高度（像素），默认 1080
        fps: 帧率，默认 30
        allow_replace: 同名草稿已存在时是否覆盖（默认否）
    """
    return registry.create(draft_name, width, height, fps, allow_replace)


@mcp.tool()
def open_draft(draft_name: str) -> dict:
    """打开磁盘上的已有草稿继续编辑（模板模式再水化）。

    内容全部保留；可继续添加新轨道内容（如 import_srt 加字幕）、查看概览、保存。
    适用于：服务器重启后续编、给剪映 GUI 里做的草稿追加字幕等。

    Args:
        draft_name: 草稿名（须已存在于剪映草稿根目录）
    """
    return registry.open(draft_name)


@mcp.tool()
def list_drafts() -> list:
    """列出剪映草稿根目录下的所有草稿。in_session=True 的可在本会话继续编辑。"""
    return registry.list()


@mcp.tool()
def delete_draft(draft_name: str) -> dict:
    """删除草稿（从磁盘移除整个草稿文件夹，不可恢复）。"""
    registry.delete(draft_name)
    return {"deleted": draft_name}


@mcp.tool()
def draft_summary(draft_name: str) -> dict:
    """查看草稿时间线概览：各轨道、每轨片段数、总时长（微秒）。"""
    return registry.summary(draft_name)


@mcp.tool()
def add_track(draft_name: str, track_type: str, track_name: str) -> dict:
    """添加命名轨道。

    Args:
        draft_name: 草稿名
        track_type: video / audio / text
        track_name: 轨道名（后续 add_*_segment 用它指定轨道）
    """
    return registry.add_track(draft_name, track_type, track_name)


@mcp.tool()
def add_video_segment(draft_name: str, video_path: str, track_name: str = "main",
                      start: TimeSpec = "0s", duration: TimeSpec = "0s") -> dict:
    """添加视频片段。

    Args:
        draft_name: 草稿名
        video_path: 视频文件绝对路径
        track_name: 目标视频轨名，默认 "main"（不存在则自动创建）
        start: 轨道上的起始时间，"5s" 或秒数
        duration: 占据轨道的时长（不是终点！），同一轨道时间段不可重叠
    """
    return registry.add_video(draft_name, video_path, track_name, start, duration)


@mcp.tool()
def add_audio_segment(draft_name: str, audio_path: str, track_name: str = "voice",
                      start: TimeSpec = "0s", duration: TimeSpec = "0s",
                      fade_in: Optional[str] = None, fade_out: Optional[str] = None) -> dict:
    """添加音频片段（可选淡入淡出）。

    Args:
        draft_name: 草稿名
        audio_path: 音频文件绝对路径
        track_name: 目标音频轨名，默认 "voice"（不存在则自动创建）
        start: 轨道上的起始时间
        duration: 占据轨道的时长（不是终点）
        fade_in: 淡入时长如 "1s"，可选
        fade_out: 淡出时长如 "2s"，可选
    """
    return registry.add_audio(draft_name, audio_path, track_name, start, duration, fade_in, fade_out)


@mcp.tool()
def add_text_segment(draft_name: str, text: str, track_name: str = "caption",
                     start: TimeSpec = "0s", duration: TimeSpec = "0s",
                     font: str = "文轩体", size: float = 5.0,
                     transform_y: float = -0.8) -> dict:
    """添加文本片段（字幕/标题）。

    Args:
        draft_name: 草稿名
        text: 文本内容
        track_name: 目标文本轨名，默认 "caption"（不存在则自动创建）
        start: 轨道上的起始时间
        duration: 占据轨道的时长（不是终点）
        font: 字体名（pyJianYingDraft FontType 枚举成员名），默认 文轩体
        size: 字号，默认 5.0
        transform_y: 垂直位置，-0.8 为画面下方（字幕位），0 为居中
    """
    return registry.add_text(draft_name, text, track_name, start, duration, font, size, transform_y)


@mcp.tool()
def import_srt(draft_name: str, srt_path: str, track_name: str = "subtitle",
               time_offset: TimeSpec = 0) -> dict:
    """从 SRT 文件导入字幕轨（轨道不存在则自动创建，默认样式模仿剪映字幕导入）。

    Args:
        draft_name: 草稿名
        srt_path: SRT 文件绝对路径
        track_name: 字幕轨名，默认 "subtitle"
        time_offset: 字幕整体时间偏移，"1s" 或秒数，默认 0
    """
    return registry.import_srt(draft_name, srt_path, track_name, time_offset)


@mcp.tool()
def build_from_storyboard(draft_name: str, shots: list, width: int = 1920, height: int = 1080,
                          fps: int = 30, allow_replace: bool = False) -> dict:
    """从分镜脚本一键生成完整草稿（视频轨＋音频轨＋字幕轨＋转场）——分镜 JSON 一步成稿。

    每个镜（shots 列表元素）的字段：
    - duration（必需）：镜头时长，"5s" 或秒数
    - video（必需）：画面素材绝对路径（素材时长须不小于镜头时长）
    - audio（可选）：旁白音频绝对路径（按自然时长放置，须不超过镜头时长）
    - caption（可选）：本镜字幕文本
    - transition（可选）：转场名（加在本镜末尾衔接下一镜，如 "信号故障"）

    先全量校验再构建：任一镜的素材缺失/时长越界/转场名错误都会在建稿前报错。
    镜头按顺序首尾相接（整数微秒累加，无舍入缝隙）。

    Args:
        draft_name: 草稿名
        shots: 分镜列表（如上字段）
        width: 视频宽度，默认 1920
        height: 视频高度，默认 1080
        fps: 帧率，默认 30
        allow_replace: 同名草稿是否覆盖，默认否
    """
    return registry.build_from_storyboard(draft_name, shots, width, height, fps, allow_replace)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
