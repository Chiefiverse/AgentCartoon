# AgentCartoon

AI 辅助的 Synfig 二维角色动画工具：用参数化动作模板生成骨骼关键帧，导出标准 `.sif`，
再在 Synfig Studio 里继续手工润色。当前进度：Phase 1（数据模型 + .sif 读写 + CLI + PyQt6 GUI）
已通，Phase 2 里程碑 1（IK 拖拽、关键帧抽稀、自动质检）已落地。

```
数据模型(骨骼/关键帧) → 动作模板 → IK/抽稀/质检编辑 → .sif XML → Synfig CLI 渲染 → 人工继续 K 帧
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

改完 `core/sif.py` 或 `core/actions.py` 后跑一下冒烟自检（23 项，约 30 秒）：

```bash
./tests/smoke.sh              # 含 Synfig 加载与逐帧差异
NO_RENDER=1 ./tests/smoke.sh  # 本机没有 Synfig 时只跑结构检查
```

不碰 Synfig、不碰网络的单元测试（64 项，含 offscreen Qt 的真实鼠标交互）：

```bash
.venv/bin/python tests/_run.py            # 全部
.venv/bin/python tests/_run.py test_ik    # 单跑一卷
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
| `--extract` | 抽稀关键帧（只减不改动作），如 `walk` 13 → 9 个 K |
| `--extract-threshold 度` | 抽稀角度阈值，默认 `8`；给了它就等于 `--extract`，`0` = 每帧都留 |
| `--extract-keep-all` | 抽稀时保留全部原始 K，只补细节不删 |
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
  手/脚末端画了绿色空心圈，拖它是 **IK**——整条肢链（大臂→小臂→手）一起动把手尖送到鼠标处，
  受关节限位约束；按骨体中段仍是单骨 FK。状态栏实时写明这一拖是 IK 还是 FK。
  洋葱皮显示前后各 2 帧。
- **骨骼树**：层级 + 当前帧本地角度，数值可直接编辑；根骨为平移不参与旋转。
- **时间轴**：滚轮缩放帧距，点击/拖动移动播放头，点击轨道行选中骨骼；
  蓝色菱形 = 模板/AI 生成的关键帧，红色圆点 = 手动 K 的帧；
  标尺底边的橙色带 = 质检 warn、红色带 = 质检 error（整段问题画成一条带）。
- **自动质检**：每次生成/编辑/撤销后跑一遍五项检测（关节超限、部件穿模、脚部滑动、
  循环不闭合、根骨漂移），结论画在时间轴上，摘要进状态栏。
- **右侧面板**：模板按钮 → 参数滑块（读 `ParamDef` 元数据自动布局）→ 生成关键帧。
  对话输入框目前只做模板名匹配，自然语言生成属于 Phase 2 里程碑 3。
- **抽稀**：`编辑 → 抽稀关键帧…`（`Ctrl+K`）问一次角度阈值，把每 2 帧一 K 的模板输出压稀
  （走路 13 → 9 个 K，与原曲线最大偏差 5.22°），走撤销栈，不满意 `Ctrl+Z` 退回。
- **快捷键**：`Ctrl+Z / Ctrl+Shift+Z` 撤销重做（快照式，覆盖生成、改角度、拖骨、抽稀、打开、保存），
  `Ctrl+N/O/S`、`F5` 渲染当前帧预览、`F6` 渲染 PNG 序列。

## 代码结构

```
agentcartoon/
├── cli.py                 # 命令行入口
├── core/
│   ├── model.py           # Point / Bone / SkeletonRig / Keyframe / AnimationClip
│   ├── actions.py         # idle walk run wave jump 五个参数化模板
│   ├── ik.py              # 四肢链的反向运动学（闭式解 + 关节限位）
│   ├── tweak.py           # 关键帧抽稀（对插值曲线递归二分，误差有上界可证）
│   ├── quality.py         # 五项自动质检（超限/穿模/滑步/闭合/漂移）
│   ├── sif.py             # .sif 写出器 + 解析器（含内嵌 JSON 元数据）
│   └── render.py          # synfig 命令行封装（找不到时抛 RenderUnavailable）
└── gui/
    ├── main_window.py     # 主窗口、撤销栈、质检接线、后台渲染线程
    ├── canvas_view.py     # 画布自绘 + 拖骨（FK）+ 拖末端（IK）
    ├── skeleton_tree.py   # 左侧骨骼树
    ├── timeline_widget.py # 底部时间轴 + 关键帧标记 + 质检色标
    └── action_panel.py    # 右侧模板与参数

tests/
├── _run.py                # 零依赖跑法：.venv/bin/python tests/_run.py
├── gui_harness.py         # offscreen Qt 脚手架（真实鼠标事件 + 坐标换算）
├── test_ik.py             # 反解、限位、超距拉直、退化
├── test_tweak.py          # 抽稀的误差上界、极值、闭合、.sif 往返
├── test_quality.py        # 五项检测：模板零误报 + 每项一个正例
├── test_undo.py           # 拖骨可撤销、根位移不被抹掉
├── test_gui_edit.py       # IK/FK 分派、色标真的画出来（读绘制结果断言）
└── smoke.sh               # 端到端：CLI → .sif → Synfig 出图 → 逐帧差异
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
5. **IK 用闭式解而不是设计文档里的 FABRIK**：四肢链只有 3 根骨、末端还挂着限位，
   FABRIK 的"限位即投影"在这种短链上实测发散（走路第 0 帧把左脚往前挪 0.3 单位，
   逐轮误差从 0.21 涨到 0.34）；换 CCD 不发散但会卡在限位带边界差 0.24~0.34 单位
   （约 20~27px，肉眼可见）。改成"2 骨前缀余弦定理解析 + 末端骨只平移"后不迭代、
   没有局部极小、两个弯曲分支按限位带和当前姿态取舍。代价是只支持 2~3 骨的链。
6. **抽稀的误差是对着插值曲线量出来的**：不是"两 K 之间角度差小于阈值就删"，而是拿
   片段自身的 CLAMPED 插值做基准递归二分，保证任意一帧与原始曲线的偏差都低于阈值
   （`tweak.max_error` 就是那个可验证的上界）。

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
- IK 只支持**四肢的 2~3 骨链**（闭式解），不是任意链长的通用 FABRIK；脊柱/颈部拖不动末端。
- 质检的阈值是对现有五个模板标定出来的：**滑步**这一项它们全都过不了（walk 5 处、run 2 处、
  jump 8 处，idle/wave 干净），也就是说模板本身还没有脚部锁定（foot lock）。这是下一步要修的，
  在那之前 `foot_slide` 只能当提示用，不能当门槛用。
- 抽稀是 opt-in（CLI `--extract` / GUI `Ctrl+K`），模板默认输出仍是每 2 帧一 K。
- GUI 的画布是自绘模型（`world_transforms()`），与 Synfig 渲染结果同源但未做逐帧比对。
- AI 对话面板只做模板名匹配；角色图片绑定、提示词生成动作、视频驱动均未开始。

## 路线图

| 阶段 | 内容 |
| --- | --- |
| Phase 1 | 数据模型、动作模板、`.sif` 读写、CLI、PyQt6 GUI ✅ |
| Phase 2 里程碑 1 | IK 拖拽、关键帧抽稀、五项自动质检、拖骨可撤销 ✅（当前） |
| Phase 2 里程碑 2 | 角色图片 → 纸片人绑定（切图 / 生成部件图两条路） |
| Phase 2 里程碑 3 | 提示词（+ 角色图）→ 动作序列，多动作拼接与质检闭环 |
| 升级版 | 视频驱动（MediaPipe）、RIFE 补帧 |
| Phase 3 | 图像自动骨骼绑定、皮肤权重 |
| Phase 4 | 打磨与生态（动作库、插件化） |

## 许可证

本项目以 **AGPL-3.0-only**（GNU Affero General Public License v3）发布，全文见 [LICENSE](LICENSE)。

要点：

- 你可以自由使用、修改、分发本代码，包括商用。
- **但**：分发修改版，或以本代码（含修改版）对外提供网络服务时，必须同样以 AGPL 公开完整对应源码。
  这条"通过网络交互也算分发"是 AGPL 相对 GPLv3 的关键差别。
- 需要把本代码嵌入**闭源产品或闭源 SaaS** 而不履行 AGPL 开源义务 → 请购买商业许可证，
  通过 [GitHub Issues](https://github.com/Chiefiverse/AgentCartoon/issues) 联系。

### 商业双授权

本仓库采用 AGPL + 商业许可的双授权模式（同 GitLab、Qt 的路子）。个人学习、学术研究、
开源项目都走 AGPL，不需要任何额外授权；只有"想用但不愿开源"的场景才需要商业许可证。

### 贡献

提交 PR 即表示你同意：你的贡献以 AGPL-3.0 授权给所有人，**同时**授权项目维护者以其他
（含商业）许可再发布该贡献。若你不代表自己所在机构持有该权利，请先确认职务作品条款。
