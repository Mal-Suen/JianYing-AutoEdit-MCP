# 剪映自动化剪辑（JianYing-AutoEdit-MCP）

让 AI 通过 MCP 协议直接组装剪映草稿：对话式建稿、加素材、导字幕，或用一条分镜脚本一键成稿。底层 [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft)（剪映 11.1 实测兼容）。

## 能力

- 草稿管理：create_draft / open_draft / list_drafts / delete_draft / draft_summary
- 内容：视频、音频、文本片段；SRT 字幕一键导入（自动建轨）
- 一键成稿：build_from_storyboard——分镜 JSON 一次生成完整草稿（视频轨＋音频轨＋字幕轨＋转场；先全量校验再构建，整数微秒时间轴无缝衔接）

## 架构：草稿即状态

服务器进程内直接持有草稿对象，每次修改立即落盘到剪映草稿目录——剪映随时刷新可见，错误当场暴露。无中间索引、无导出重放。

## 快速开始

### 从源码运行（PyPI 发布前）

```bash
git clone https://github.com/Mal-Suen/JianYing-AutoEdit-MCP.git
pip install "pyJianYingDraft>=0.3.0" "mcp>=1.2.0,<2"
```

MCP 客户端配置（以 Qwen Code / Claude Code 等 stdio 客户端为例）：

```json
{
  "mcpServers": {
    "jianying-autoedit": {
      "command": "python",
      "args": ["-m", "jianying_autoedit.server"],
      "env": {
        "PYTHONPATH": "/path/to/JianYing-AutoEdit-MCP",
        "JY_DRAFT_ROOT": "/path/to/你的剪映草稿目录"
      }
    }
  }
}
```

`JY_DRAFT_ROOT` 不设置时会自动探测常见位置；找不到时设置该变量指向剪映的草稿文件夹即可。

### 一键成稿示例

```json
{
  "draft_name": "我的视频",
  "shots": [
    {
      "duration": "5s",
      "video": "D:/materials/shot01.mp4",
      "audio": "D:/materials/vo01.mp3",
      "caption": "第一镜字幕",
      "transition": "信号故障"
    },
    {
      "duration": "8s",
      "video": "D:/materials/shot02.mp4",
      "caption": "第二镜字幕"
    }
  ]
}
```

一次调用生成完整草稿：视频轨（镜头首尾相接）＋音频轨（旁白按自然时长放置）＋字幕轨＋转场。任一镜的素材缺失、时长越界、转场名错误都会在建稿前报错，不留半成品。

## 环境要求

| 依赖 | 最低版本 | 说明 |
|---|---|---|
| Python | 3.10 | 受 mcp 1.x 限制（pyJianYingDraft 本身支持 3.8+） |
| 剪映 | 5.9 | 草稿生成功能的下限（pyJianYingDraft 官方测试锚点）；已在剪映 11.1.0.14287 实测通过，更老版本未验证 |
| mcp | 1.2（钉 <2） | 2.x 将 FastMCP 改名 MCPServer、API 不兼容，迁移列入后续计划 |

**剪映版本兼容是单向的**：新版剪映能打开旧格式草稿（11.1 可读 0.3.0 写的 360000 格式，已实测）；旧版剪映打不开更新的格式。未来新版剪映大概率继续向下兼容，但每个大版本需实测确认后加入支持矩阵。

**平台差异**：
- Windows：全功能（草稿生成；自动导出仅剪映 ≤6）
- Linux/macOS：草稿生成可用（Linux 需系统安装 libmediainfo，pymediainfo 依赖它），但生成的草稿仍需在 Windows 版剪映中打开导出；Mac 草稿目录自动探测待验证，可用环境变量 `JY_DRAFT_ROOT` 指定

## 限制（诚实标注）

1. 自动导出成片仅支持剪映 ≤6（JianyingController 硬限制）；剪映 11.1 上导出需在剪映里手动完成
2. 打开已有草稿（open_draft）为模板模式：内容全部保留、可加新轨道内容（如字幕）；不能向已有轨道添加片段（完整再水化列为后续特性）；加密版本剪映（如 10.8）保存的草稿无法打开
3. Mac 支持待验证

## 致谢

- [GuanYixuan/pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft)——底层剪映草稿生成库，本项目的地基
- [hey-jian-wei/jianying-mcp](https://github.com/hey-jian-wei/jianying-mcp)——立项时的思路参考（未使用其代码；本项目的「草稿即状态」架构为独立设计，规避了索引＋重放的状态漂移问题）

## License

GPL-3.0
