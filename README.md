# AgentCartoon

AI 辅助的 Synfig 二维角色动画工具：用参数化动作模板生成骨骼关键帧，导出标准 `.sif`，
再在 Synfig Studio 里继续手工润色。当前为 Phase 1（MVP）：数据模型 + .sif 读写 + CLI + PyQt6 GUI。

```
数据模型(骨骼/关键帧) → 动作模板 → .sif XML → Synfig CLI 渲染 → 人工继续 K 帧
```

## 环境要求

- Python 3.10+（开发验证于 3.14）
- PyQt6、lxml
- 可选：本机安装 [Synfig Studio](https://www.synfig.org/) 1.4.5+，用于渲染预览与出图。
  命令行路径可用环境变量覆盖：`export AC_SYNFIG_BIN=/usr/bin/synfig`

## 安装与运行

```bash
./run.sh setup                                   # 建 venv 并装依赖
./run.sh cli --action walk --out walk.sif        # 命令行
./run.sh gui                                     # 桌面端

# 或者
pip install -e .
agentcartoon --action walk --params "stride=60,speed=normal" --duration 3s --out walk_demo.sif
agentcartoon-gui
```

## 命令行

```bash
python -m agentcartoon.cli \
    --template stickman --action walk \
    --params "stride=60,speed=normal" \
    --duration 3s --fps 24 --key-step 2 \
    --out walk_demo.sif [--render 输出目录]
```

| 选项 | 说明 |
| --- | --- |
| `--template` | 角色模板，目前仅 `stickman`（15 骨人形） |
| `--action` | 动作 key：`idle` / `walk` / `run` / `wave` / `jump` |
| `--params` | 逗号分隔的 `k=v`，枚举值支持 `speed=slow\|normal\|fast`、`hand=left\|right` |
| `--duration` | 秒数（`3s` 或 `3`），省略则用模板默认时长 |
| `--key-step` | 每隔多少帧打一个关键帧，默认 2 |
| `--render [DIR]` | 调本机 synfig 出 PNG 序列；未安装时降级为只写 `.sif` |
| `--list-actions` | 列出全部动作模板及其参数范围/默认值 |

## 桌面端

```
┌──────────┬────────────────────┬─────────────┬──────────┐
│ 骨骼树    │      画布           │  AI 对话     │  Synfig  │
│ +角度编辑 │  拖骨/缩放/洋葱皮    │  模板按钮    │  渲染预览 │
│          │                    │  参数滑块     │          │
├──────────┴────────────────────┴─────────────┴──────────
│        时间轴：帧标尺 + 每骨轨道 + 关键帧标记 + 播放         │
└─────────────────────────────────────────────────────────┘
```

- **画布**：滚轮缩放，空白处拖动平移，点骨骼选中，拖动骨骼改角度（FK，子骨跟随）。
  洋葱皮显示前后各 2 帧。
- **骨骼树**：层级 + 当前帧本地角度，数值可直接编辑；根骨为平移不参与旋转。
- **时间轴**：滚轮缩放帧距，点击/拖动移动播放头，点击轨道行选中骨骼；
  蓝色菱形 = 模板/AI 生成的关键帧，红色圆点 = 手动 K 的帧。
- **右侧面板**：模板按钮 → 参数滑块（读 `ParamDef` 元数据自动布局）→ 生成关键帧。
  对话输入框目前只做模板名匹配，自然语言生成属于 Phase 2。
- **快捷键**：`Ctrl+Z / Ctrl+Shift+Z` 撤销重做（快照式，覆盖生成、改角度、打开、保存），
  `Ctrl+N/O/S`、`F5` 渲染当前帧预览、`F6` 渲染 PNG 序列。

## 代码结构

```
agentcartoon/
├── cli.py                 # 命令行入口
├── core/
│   ├── model.py           # Point / Bone / SkeletonRig / Keyframe / AnimationClip
│   ├── actions.py         # idle walk run wave jump 五个参数化模板
│   ├── sif.py             # .sif 写出器 + 解析器（含内嵌 JSON 元数据）
│   └── render.py          # synfig 命令行封装（找不到时抛 RenderUnavailable）
└── gui/
    ├── main_window.py     # 主窗口、撤销栈、后台渲染线程
    ├── canvas_view.py     # 画布自绘 + 拖骨
    ├── skeleton_tree.py   # 左侧骨骼树
    ├── timeline_widget.py # 底部时间轴
    └── action_panel.py    # 右侧模板与参数
```

## 关键设计决策

1. **不用 Synfig 原生 `skeleton` + `skeleton_deformation` 层**，改用 FK transform 层 rig：
   每根骨骼 = 一个 `group` 层，内含 `rotate`（非根）或 `translate`（根）+ 图形层。
   官方 `add-skeleton-simple` 插件里的 `stickman.sif` 同样是 transform 层搭的，佐证这条路可行。
2. **文件驱动**，不侵入 Synfig 进程；导出的是标准 `.sif`，可在 Synfig 里手动继续 K 帧。
3. **角度约定**：度、逆时针为正、Y 轴向上；世界角 = 父世界角 + 本地角；
   导出时 rotate 的 `amount = 当前本地角 - 静止本地角`（与 Synfig rotate 语义一致）。
4. **双向读取**：解析 `.sif` 时优先读 `<meta name="agentcartoon">` 内嵌的 JSON
   （完整 rig+clip+canvas，精确回读）；没有内嵌数据时退回结构解析 / 原生 skeleton 只读解析。
   这样文件既能在 Synfig 里编辑，也能被本工具重新打开。

## Synfig 1.4.5 `.sif` 格式踩坑（实测）

命令行用法：`synfig -q -i in.sif -o out.ppm -t ppm --time 0f -a 4`（`--output=` 写法会报 Unknown option）。

- 同一 canvas 里**不允许直接嵌 `<layer>` 子元素**；组层的孩子必须写在 `<param name="canvas"><canvas>…</canvas></param>` 里。
- 静态值写 `<param><real|vector|color …/></param>`；**动画值**必须写 `<param><animated type="angle">…</animated></param>`。
  用 `<angle value=…>` 里套 `<waypoint>` 会被当成静态值，动画被整体忽略。
- `<waypoint>` 必须带一个子值元素，否则报 `waypoint is missing its data`。
- `<vector>` 用子元素 `<x>`/`<y>`，不能用 `x=`/`y=` 属性。
- transform 层作用于**同一 canvas 中排在它之前的兄弟层**，所以骨骼 group 内顺序是：先图形层，后 rotate/translate 层。
- 时间码统一用 `Nf`（fps 取自 canvas 的 `fps` 属性，`begin-time="0f"`、`end-time="(last+1)f"`）。
- `<canvas>` 里**不能出现 `<nowarp>`**（`Unexpected element`，整个文件加载失败）；
  group 层的 `outline_grow` 是 `<real>` 而不是 `<bool>`。
- 原生 `skeleton` 层的 `bones` 列表写法未攻克（只读能解析），见上面的决策 1。

## 已知局限

- MVP 导出的是 **transform-rig**，不是 Synfig 骨骼变形层。要用皮肤权重（skin weights）
  需在 Synfig 里手动添加 `skeleton_deformation`。
- 模板生成的关键帧是每 `key_step` 帧一 K，还没做关键帧抽取（阈值 Δθ>8°）。
- GUI 的画布是自绘模型（`world_transforms()`），与 Synfig 渲染结果同源但未做逐帧比对。
- AI 对话面板只做模板名匹配；MediaPipe 视频驱动、FABRIK IK、RIFE 补帧、质量检测均在 Phase 2。

## 路线图

| 阶段 | 内容 |
| --- | --- |
| Phase 1（当前） | 数据模型、动作模板、`.sif` 读写、CLI、PyQt6 GUI |
| Phase 2 | MediaPipe 视频驱动关键帧、FABRIK IK、RIFE 补帧、质量检测 Agent |
| Phase 3 | 图像自动骨骼绑定、皮肤权重 |
| Phase 4 | 打磨与生态（动作库、插件化） |
