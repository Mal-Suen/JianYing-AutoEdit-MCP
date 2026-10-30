# -*- coding: utf-8 -*-
"""草稿即状态核心：进程内持有 ScriptFile 对象，每次修改立即落盘。

与「索引＋导出重放」架构的区别：磁盘上的草稿就是唯一状态源，
剪映随时刷新可见，错误当场暴露，无中间索引可漂移。

再水化（open）：基于库的模板模式加载已有草稿——内容全部保留为
「导入轨道」，可继续添加新轨道内容并保存；v1 不能向导入轨道加片段。
"""

import json
import os
import shutil
from typing import Dict, Optional, Union

import pyJianYingDraft as draft
from pyJianYingDraft import ClipSettings, FontType, TextStyle, trange
from pyJianYingDraft.exceptions import DraftContentLoadFailed

_TRACK_TYPES = {
    "video": draft.TrackType.video,
    "audio": draft.TrackType.audio,
    "text": draft.TrackType.text,
}

TimeSpec = Union[str, float]

# 草稿名即磁盘目录名：禁止路径成分与 Windows 保留字符，堵住路径穿越
_INVALID_DRAFT_NAME_CHARS = set('<>:"|?*\0')


def validate_draft_name(name: str) -> str:
    """校验草稿名：只允许作为单个目录名的字符串，返回原值或抛 ValueError。"""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("草稿名必须是非空字符串")
    if len(name) > 100:
        raise ValueError(f"草稿名过长（{len(name)} > 100）: {name!r}")
    if name != name.strip() or name != name.strip(". "):
        raise ValueError(f"草稿名首尾不能是空格或点号: {name!r}")
    bad = _INVALID_DRAFT_NAME_CHARS.intersection(name)
    if bad:
        raise ValueError(f"草稿名含非法字符 {sorted(bad)}: {name!r}")
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"草稿名不能含路径成分: {name!r}")
    if name in {".", ".."}:
        raise ValueError(f"草稿名不能是 . 或 ..: {name!r}")
    return name


def _parse_time(spec: TimeSpec, field: str, *, positive: bool = False) -> int:
    """把时间规格解析为微秒并校验：非法串或归零结果抛 ValueError。

    裸数字按微秒计（与 pyJianYingDraft.tim 一致，1 秒＝1000000）；
    字符串须含 h/m/s 单位字母，否则 tim() 会静默归零（如「5秒」），在此提前拦截。
    positive=True 时结果须为正（用于时长）。
    """
    if isinstance(spec, bool) or not isinstance(spec, (str, int, float)):
        raise ValueError(f"{field} 无法解析的时间值 {spec!r}：须为时间串或微秒数")
    if isinstance(spec, str):
        s = spec.strip().lower().lstrip("+-")
        if not s or not any(u in s for u in ("h", "m", "s")):
            raise ValueError(
                f"{field} 无法解析的时间串 {spec!r}：须形如 \"5s\"、\"1m30s\"，或直接给微秒数"
            )
    try:
        us = draft.tim(spec)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{field} 无法解析的时间值 {spec!r}: {e}")
    if positive and us <= 0:
        raise ValueError(f"{field} 必须为正时长，得到 {spec!r} -> {us}μs")
    return us


def resolve_draft_root() -> str:
    """剪映草稿根目录：环境变量 JY_DRAFT_ROOT 优先，其次探测常见位置。"""
    env = os.getenv("JY_DRAFT_ROOT")
    if env:
        if os.path.isdir(env):
            return env
        raise FileNotFoundError(f"JY_DRAFT_ROOT 指向的路径不存在: {env}")
    candidates = [
        r"D:\Program Files\JianyingPro Drafts",
        r"C:\Program Files\JianyingPro Drafts",
        os.path.expandvars(r"%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    raise FileNotFoundError("未找到剪映草稿根目录；请设置环境变量 JY_DRAFT_ROOT 指向剪映草稿文件夹")


class DraftRegistry:
    """草稿注册表：name -> ScriptFile（进程内存活）。

    每个草稿记录「上次加载或保存后」主 JSON（draft_content.json）的 mtime；
    save 前重新 stat，不一致说明磁盘被外部改过（典型是剪映 GUI 保存），
    拒绝覆盖并要求先重新 open，避免静默丢弃外部编辑。
    """

    def __init__(self, root: Optional[str] = None):
        self.root = root or resolve_draft_root()
        self.folder = draft.DraftFolder(self.root)
        self._scripts: Dict[str, draft.ScriptFile] = {}
        self._mtimes: Dict[str, float] = {}

    # ---- 落盘与外部修改检测 ----

    @staticmethod
    def _stat_mtime(path: str) -> Optional[float]:
        """取文件 mtime；文件不存在返回 None。"""
        try:
            return os.stat(path).st_mtime
        except OSError:
            return None

    @classmethod
    def _script_mtime(cls, script: draft.ScriptFile) -> Optional[float]:
        """草稿主 JSON（draft_content.json）的 mtime；不可 stat 时退化为草稿目录 mtime。"""
        path = getattr(script, "save_path", None)
        if not path:
            return None
        mtime = cls._stat_mtime(path)
        if mtime is None:
            mtime = cls._stat_mtime(os.path.dirname(path))
        return mtime

    def _record_mtime(self, name: str, script: draft.ScriptFile) -> None:
        """记录草稿「上次加载或保存后」的主 JSON mtime。"""
        self._mtimes[name] = self._script_mtime(script)

    def _check_external_change(self, name: str, script: draft.ScriptFile) -> None:
        """save 前校验磁盘未被外部修改：mtime 与记录不符则拒写。"""
        recorded = self._mtimes.get(name)
        if recorded is None:
            return
        if self._script_mtime(script) != recorded:
            raise ValueError(
                f"草稿 '{name}' 的磁盘文件在打开/上次保存后被外部修改过"
                f"（可能是剪映 GUI 保存了它）。为避免覆盖外部编辑，已拒绝本次写入；"
                f"请先重新 open('{name}') 加载最新内容，再继续编辑"
            )

    def _save(self, name: str, script: draft.ScriptFile) -> None:
        """带保护的保存：外部修改检测 -> 备份主 JSON -> save -> 校验可解析 -> 更新 mtime。

        库的 save() 是截断全量覆盖写（非原子）；若写入中断会留半截 JSON。
        此处在写前把主 JSON 复制为 .bak，save 后校验文件可解析，
        失败则从 .bak 恢复；.bak 只在完全成功后删除（恢复失败时它是唯一好副本）。
        """
        self._check_external_change(name, script)
        path = getattr(script, "save_path", None)
        bak = path + ".jyae.bak" if path else None
        had_main = bool(path) and os.path.exists(path)
        if had_main:
            shutil.copy2(path, bak)
        try:
            script.save()
        except Exception:
            if had_main and os.path.exists(bak):
                shutil.copy2(bak, path)
            raise
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    json.load(f)
            except Exception:
                if had_main and os.path.exists(bak):
                    shutil.copy2(bak, path)
                raise ValueError(
                    f"草稿 '{name}' 保存后校验失败（draft_content.json 不可解析），"
                    f"已从备份恢复；请重试或检查磁盘状态"
                )
        if had_main and os.path.exists(bak):
            try:
                os.remove(bak)
            except OSError:
                pass
        self._record_mtime(name, script)

    # ---- 草稿生命周期 ----

    def create(self, name: str, width: int = 1920, height: int = 1080,
               fps: int = 30, allow_replace: bool = False) -> dict:
        validate_draft_name(name)
        script = self.folder.create_draft(name, width, height, fps, allow_replace=allow_replace)
        self._mtimes.pop(name, None)  # 重建后旧记录作废，避免误判外部修改
        self._save(name, script)  # 立即落盘并注册进剪映草稿列表
        self._scripts[name] = script
        return {"draft_name": name, "width": width, "height": height, "fps": fps, "saved": True}

    def open(self, name: str) -> dict:
        """打开磁盘上的已有草稿（模板模式再水化）。

        内容全部保留；可继续添加新轨道内容（字幕、新素材轨）并保存。
        v1 边界：不能向已有轨道添加片段；加密草稿（如剪映 10.8 保存的）无法打开。
        """
        validate_draft_name(name)
        if name in self._scripts:
            script = self._scripts[name]
            recorded = self._mtimes.get(name)
            if recorded is None or self._script_mtime(script) == recorded:
                return {"draft_name": name, "opened": True, "already_open": True}
            # 磁盘被外部改过（如剪映 GUI 保存）：不走幂等短路，重新加载最新内容，
            # 与 _save 拒写提示「先重新 open」配套。
        try:
            script = self.folder.load_template(name)
        except DraftContentLoadFailed as e:
            raise ValueError(
                f"草稿 '{name}' 无法按明文 JSON 读取（可能是加密版本剪映保存的，如 10.8）；"
                f"v1 不支持加密草稿。原始错误: {e}"
            )
        self._scripts[name] = script
        self._record_mtime(name, script)
        return {"draft_name": name, "opened": True, "mode": "template",
                "note": "已有轨道保留但不可加片段，新内容请用新轨道名"}

    def get(self, name: str) -> draft.ScriptFile:
        if name not in self._scripts:
            raise KeyError(
                f"草稿 '{name}' 不在本会话中。可先用 open_draft 打开（明文草稿），"
                f"或 list_drafts 查看磁盘草稿，或 create(allow_replace=True) 重建"
            )
        return self._scripts[name]

    def list(self) -> list:
        return [{"name": n, "in_session": n in self._scripts} for n in self.folder.list_drafts()]

    def delete(self, name: str) -> None:
        validate_draft_name(name)
        self.folder.remove(name)
        self._scripts.pop(name, None)
        self._mtimes.pop(name, None)

    # ---- 轨道与内容 ----

    def add_track(self, name: str, track_type: str, track_name: str) -> dict:
        script = self.get(name)
        if track_type not in _TRACK_TYPES:
            raise ValueError(f"track_type 必须是 {list(_TRACK_TYPES)} 之一，收到: {track_type}")
        self._guard_imported_name(script, track_name)
        script.append_tracks([draft.TrackSpec(_TRACK_TYPES[track_type], track_name)])
        self._save(name, script)
        return {"draft_name": name, "track_type": track_type, "track_name": track_name}

    def _guard_imported_name(self, script, track_name: str) -> None:
        """打开的草稿里已有同名轨道时明确报错，避免静默创建重名新轨道。"""
        imported_names = [t.name for t in getattr(script, "imported_tracks", [])]
        if track_name in imported_names:
            raise ValueError(
                f"轨道 '{track_name}' 是打开草稿中的已有轨道，v1 不能向其添加片段；请换一个新轨道名"
            )

    def _ensure_track(self, script, track_type: str, track_name: str) -> None:
        self._guard_imported_name(script, track_name)
        if track_name not in script.tracks:
            if track_type not in _TRACK_TYPES:
                raise ValueError(f"track_type 必须是 {list(_TRACK_TYPES)} 之一，收到: {track_type}")
            script.append_tracks([draft.TrackSpec(_TRACK_TYPES[track_type], track_name)])

    def add_video(self, name: str, path: str, track_name: str = "main",
                  start: TimeSpec = "0s", duration: TimeSpec = "0s") -> dict:
        script = self.get(name)
        self._ensure_track(script, "video", track_name)
        seg = draft.VideoSegment(path, trange(_parse_time(start, "start"),
                                              _parse_time(duration, "duration", positive=True)))
        script.add_segment(seg, track_name)
        self._save(name, script)
        return {"draft_name": name, "track": track_name, "start": str(start),
                "duration": str(duration), "path": path}

    def add_audio(self, name: str, path: str, track_name: str = "voice",
                  start: TimeSpec = "0s", duration: TimeSpec = "0s",
                  fade_in: Optional[str] = None, fade_out: Optional[str] = None) -> dict:
        script = self.get(name)
        self._ensure_track(script, "audio", track_name)
        seg = draft.AudioSegment(path, trange(_parse_time(start, "start"),
                                              _parse_time(duration, "duration", positive=True)))
        if fade_in or fade_out:
            seg.add_fade(fade_in or "0s", fade_out or "0s")
        script.add_segment(seg, track_name)
        self._save(name, script)
        return {"draft_name": name, "track": track_name, "start": str(start),
                "duration": str(duration), "path": path,
                "fade_in": fade_in, "fade_out": fade_out}

    def add_text(self, name: str, text: str, track_name: str = "caption",
                 start: TimeSpec = "0s", duration: TimeSpec = "0s",
                 font: str = "文轩体", size: float = 5.0,
                 transform_y: float = -0.8) -> dict:
        script = self.get(name)
        self._ensure_track(script, "text", track_name)
        try:
            font_enum = FontType[font]
        except KeyError:
            raise ValueError(f"未知字体 '{font}'；可用字体见 pyJianYingDraft 的 FontType 枚举")
        seg = draft.TextSegment(
            text, trange(_parse_time(start, "start"),
                         _parse_time(duration, "duration", positive=True)),
            font=font_enum,
            style=TextStyle(size=size, align=1, auto_wrapping=True),
            clip_settings=ClipSettings(transform_y=transform_y),
        )
        script.add_segment(seg, track_name)
        self._save(name, script)
        return {"draft_name": name, "track": track_name, "start": str(start),
                "duration": str(duration), "text": text}

    def import_srt(self, name: str, srt_path: str, track_name: str = "subtitle",
                   time_offset: TimeSpec = 0) -> dict:
        script = self.get(name)
        self._guard_imported_name(script, track_name)
        script.import_srt(srt_path, track_name,
                          time_offset=_parse_time(time_offset, "time_offset"))
        self._save(name, script)
        return {"draft_name": name, "track": track_name, "srt_path": srt_path}

    def build_from_storyboard(self, name: str, shots: list, width: int = 1920,
                              height: int = 1080, fps: int = 30,
                              allow_replace: bool = False) -> dict:
        """从分镜脚本一键成稿：视频轨＋音频轨＋字幕轨＋转场，一次生成完整草稿。

        每个镜（shots 列表元素）的字段：
        - duration（必需）：镜头时长，"5s" 或微秒数（1 秒＝1000000）
        - video（必需）：画面素材绝对路径（素材时长须不小于镜头时长）
        - audio（可选）：旁白音频绝对路径（按自然时长放置，须不超过镜头时长）
        - caption（可选）：本镜字幕文本
        - transition（可选）：转场名（加在本镜末尾衔接下一镜，TransitionType 枚举名；
          最后一镜的 transition 会被忽略——转场需要下一镜才有意义）

        两遍式：先全量校验（任一镜失败则不建稿），再构建。
        时间轴用整数微秒累加，规避 trange 分别舍入导致的背靠背 1μs 重叠（上游 issue #199）。
        """
        if not shots:
            raise ValueError("shots 不能为空")

        parsed = []
        for i, shot in enumerate(shots):
            idx = i + 1
            if "duration" not in shot:
                raise ValueError(f"镜 {idx} 缺少 duration 字段")
            dur = _parse_time(shot["duration"], f"镜 {idx} 的 duration", positive=True)
            video_path = shot.get("video")
            if not video_path:
                raise ValueError(f"镜 {idx} 缺少 video 素材路径")
            if not os.path.exists(video_path):
                raise ValueError(f"镜 {idx} 视频文件不存在: {video_path}")
            video_material = draft.VideoMaterial(video_path)
            if video_material.duration < dur:
                raise ValueError(
                    f"镜 {idx} 时长 {dur}μs 超过视频素材时长 {video_material.duration}μs: {video_path}")
            audio_material = None
            audio_path = shot.get("audio")
            if audio_path:
                if not os.path.exists(audio_path):
                    raise ValueError(f"镜 {idx} 音频文件不存在: {audio_path}")
                audio_material = draft.AudioMaterial(audio_path)
                if audio_material.duration > dur:
                    raise ValueError(
                        f"镜 {idx} 旁白时长 {audio_material.duration}μs 超过镜时长 {dur}μs: {audio_path}")
            transition = shot.get("transition")
            if transition:
                try:
                    draft.TransitionType[transition]
                except KeyError:
                    raise ValueError(
                        f"镜 {idx} 未知转场 '{transition}'；可用转场见 pyJianYingDraft 的 TransitionType 枚举")
            parsed.append((dur, video_material, audio_material,
                           shot.get("caption"), transition))

        # allow_replace=True 时库会先删原稿再建新稿；先把原稿目录备份，
        # 构建或落盘失败则自动恢复，成功后删除备份（原稿不因中途异常而失）。
        draft_path = os.path.join(self.root, name)
        backup_path = draft_path + ".jyae_backup"
        replaced = allow_replace and os.path.exists(draft_path)
        if replaced:
            if os.path.exists(backup_path):
                shutil.rmtree(backup_path)
            shutil.copytree(draft_path, backup_path)
        try:
            self.create(name, width, height, fps, allow_replace)
            script = self.get(name)
            script.append_tracks([
                draft.TrackSpec(draft.TrackType.video, "main"),
                draft.TrackSpec(draft.TrackType.audio, "voice"),
                draft.TrackSpec(draft.TrackType.text, "caption"),
            ])

            t = 0
            last = len(parsed) - 1
            for i, (dur, video_material, audio_material, caption, transition) in enumerate(parsed):
                seg = draft.VideoSegment(video_material, draft.trange(t, dur))
                if transition and i != last:
                    # 末镜不加转场：转场衔接本镜与下一镜，末镜无下一镜
                    seg.add_transition(draft.TransitionType[transition])
                script.add_segment(seg, "main")
                if audio_material:
                    script.add_segment(
                        draft.AudioSegment(audio_material, draft.trange(t, audio_material.duration)), "voice")
                if caption:
                    script.add_segment(draft.TextSegment(
                        caption, draft.trange(t, dur),
                        font=FontType["文轩体"],
                        style=TextStyle(size=5.0, align=1, auto_wrapping=True),
                        clip_settings=ClipSettings(transform_y=-0.8),
                    ), "caption")
                t += dur

            self._save(name, script)
        except BaseException:
            if replaced:
                try:
                    if os.path.exists(draft_path):
                        shutil.rmtree(draft_path)
                    if os.path.exists(backup_path):
                        shutil.move(backup_path, draft_path)
                except OSError:
                    pass  # 恢复失败时保留 .jyae_backup 供人工恢复
                # 恢复后磁盘是原稿，而 _scripts 里是半成品新稿对象、_mtimes 无记录；
                # 两者都丢弃，强制下次先 open，避免无外部修改检测的静默覆盖
                self._scripts.pop(name, None)
                self._mtimes.pop(name, None)
            raise
        # 成功才删备份；恢复失败的场合 .jyae_backup 是唯一好副本，必须保留
        if replaced and os.path.exists(backup_path):
            shutil.rmtree(backup_path, ignore_errors=True)
        return self.summary(name)

    # ---- 概览 ----

    def summary(self, name: str) -> dict:
        script = self.get(name)
        tracks_out = []
        for t in getattr(script, "imported_tracks", []):
            segs = getattr(t, "segments", [])
            tracks_out.append({
                "track": str(t.name),
                "type": str(t.track_type),
                "segments": len(segs),
                "imported": True,
            })
        tracks = script.tracks
        pairs = tracks.items() if hasattr(tracks, "items") else list(enumerate(tracks))
        for key, t in pairs:
            segs = getattr(t, "segments", [])
            track_label = key if isinstance(key, str) else getattr(t, "track_name", str(key))
            tracks_out.append({
                "track": str(track_label),
                "type": str(getattr(t, "track_type", "")),
                "segments": len(segs),
                "imported": False,
            })
        return {"draft_name": name, "duration_us": getattr(script, "duration", None),
                "in_session": True, "tracks": tracks_out}
