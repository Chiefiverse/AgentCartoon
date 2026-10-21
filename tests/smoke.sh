#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Chiefi <341863326+Chiefiverse@users.noreply.github.com>
# SPDX-License-Identifier: AGPL-3.0-only
#
# AgentCartoon Phase 1 冒烟自检：CLI 生成 → 参数生效 → .sif 能被 Synfig 加载 → 动画真的在动。
# 任何改动 core/sif.py 或 core/actions.py 之后都应该跑一遍。
#
#   ./tests/smoke.sh          # 全跑（需要本机 synfig，缺失则跳过渲染类检查）
#   NO_RENDER=1 ./tests/smoke.sh   # 只跑不依赖 Synfig 的部分
set -uo pipefail

cd "$(dirname "$0")/.."
PY=".venv/bin/python"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0; skip=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); [ -n "${2:-}" ] && printf '       %s\n' "$2"; }
skip() { printf '  \033[33mSKIP\033[0m %s\n' "$1"; skip=$((skip+1)); }

[ -x "$PY" ] || { echo "找不到 $PY，先跑 ./run.sh setup"; exit 1; }

SYNFIG="$(${PY} -c 'from agentcartoon.core import render; print(render.find_synfig() or "")')"
if [ -z "$SYNFIG" ] || [ "${NO_RENDER:-0}" = "1" ]; then
    SYNFIG=""
    echo "未找到 synfig 或指定 NO_RENDER，渲染相关检查将跳过（.sif 合法性无法验证）"
fi

# ---------------------------------------------------------------- 1. CLI 生成
# 基准值来自 2026-10-21 实测；若有意调整模板，请同步更新这张表。
echo "[1/5] 五个动作模板的规模与闭合误差"
declare -A WANT_KF=( [idle]=25 [walk]=13 [run]=11 [wave]=25 [jump]=19 )
declare -A WANT_FR=( [idle]=48 [walk]=24 [run]=19 [wave]=48 [jump]=34 )

for a in idle walk run wave jump; do
    line="$($PY -m agentcartoon.cli --action "$a" --out "$TMP/$a.sif" 2>&1 | head -1)"
    [ -f "$TMP/$a.sif" ] || { bad "$a 未产出文件" "$line"; continue; }
    kf="$(sed -n 's/.*，\([0-9]\+\) 个关键帧.*/\1/p' <<<"$line")"
    fr="$(sed -n 's/.*关键帧，\([0-9]\+\) 帧.*/\1/p' <<<"$line")"
    err="$(sed -n 's/.*闭合误差 \([0-9.]*\)°.*/\1/p' <<<"$line")"
    if [ "$kf" = "${WANT_KF[$a]}" ] && [ "$fr" = "${WANT_FR[$a]}" ]; then
        ok "$a: ${kf} 关键帧 / ${fr} 帧"
    else
        bad "$a 规模变了（期望 ${WANT_KF[$a]}/${WANT_FR[$a]}，实得 ${kf}/${fr}）"
    fi
    if [ "$err" != "0.00" ]; then bad "$a 闭合误差 $err ≠ 0.00（循环动作首尾会跳）"; fi
done

# ------------------------------------------------------------ 2. 参数是否生效
echo "[2/5] 参数真的改变输出，而不是被静默忽略"
base_md5="$(md5sum < "$TMP/walk.sif")"
$PY -m agentcartoon.cli --action walk --params "stride=140,speed=fast" --duration 3s \
    --out "$TMP/walk_fast.sif" >/dev/null 2>&1
kf="$(sed -n 's/.*，\([0-9]\+\) 个关键帧.*/\1/p' <<<"$($PY -m agentcartoon.cli --action walk --params "stride=140,speed=fast" --duration 3s --out "$TMP/walk_fast.sif" 2>&1 | head -1)")"
[ "$kf" = "37" ] && ok "walk 3s/fast → 37 关键帧" || bad "walk 3s 关键帧数异常（得 ${kf}，期望 37）"
[ "$(md5sum < "$TMP/walk_fast.sif")" != "$base_md5" ] && ok "改参数后 .sif 内容不同" || bad "参数被忽略：两个 walk.sif 完全相同"

$PY -m agentcartoon.cli --action wave --params "hand=left"  --out "$TMP/l.sif" >/dev/null 2>&1
$PY -m agentcartoon.cli --action wave --params "hand=right" --out "$TMP/r.sif" >/dev/null 2>&1
if [ -n "$SYNFIG" ]; then
    "$SYNFIG" -q -i "$TMP/l.sif" -o "$TMP/l.png" -t png --time 24f -a 2 >/dev/null 2>&1
    "$SYNFIG" -q -i "$TMP/r.sif" -o "$TMP/r.png" -t png --time 24f -a 2 >/dev/null 2>&1
    if [ -f "$TMP/l.png" ] && [ -f "$TMP/r.png" ] && ! cmp -s "$TMP/l.png" "$TMP/r.png"; then
        ok "hand=left / right 渲染结果不同（抬的是不同手臂）"
    else
        bad "hand 参数未生效：left 与 right 渲染相同或渲染失败"
    fi
else
    skip "hand 参数视觉验证（无 synfig）"
fi

if $PY -m agentcartoon.cli --action walk --params "speed=nope" --out "$TMP/x.sif" >/dev/null 2>&1; then
    bad "非法枚举值竟然成功了（应报错退出）"
else
    ok "非法参数 speed=nope 正确报错退出"
fi
if $PY -m agentcartoon.cli --action nosuchaction --out "$TMP/x.sif" >/dev/null 2>&1; then
    bad "未知动作名竟然成功了"
else
    ok "未知动作正确报错退出"
fi

# ------------------------------------------------------------- 2b. 关键帧抽稀
line="$($PY -m agentcartoon.cli --action walk --extract --out "$TMP/walk_ext.sif" 2>&1 | head -1)"
ext="$(sed -n 's/.*→ \([0-9]\+\) 个关键帧.*/\1/p' <<<"$line")"
if [ -n "$ext" ] && [ "$ext" -lt "${WANT_KF[walk]}" ]; then
    ok "walk --extract → ${ext} 关键帧（原 ${WANT_KF[walk]}）"
else
    bad "walk --extract 未减下关键帧（得 ${ext}，应少于 ${WANT_KF[walk]}）：$line"
fi
if $PY -c "
import sys; from agentcartoon.core import sif
d = sif.SifDocument.load('$TMP/walk_ext.sif')
sys.exit(0 if d.clip.closure_error() < 1e-6 and len(d.rig.bones) == 15 else 1)"; then
    ok "抽稀后的 .sif 回读精确、闭合误差仍 0.00°"
else
    bad "抽稀后的 .sif 回读失败或循环被破坏"
fi
if [ -n "$SYNFIG" ]; then
    "$SYNFIG" -q -i "$TMP/walk_ext.sif" -o "$TMP/walk_ext.png" -t png --time 6f -a 2 >/dev/null 2>&1
    [ -s "$TMP/walk_ext.png" ] && ok "抽稀后的 .sif 可被 Synfig 加载并出图" \
                               || bad "抽稀后的 .sif Synfig 出图失败"
else
    skip "抽稀后 .sif 的 Synfig 验证（无 synfig）"
fi

# ---------------------------------------------------------- 3. .sif 合法性检查
echo "[3/5] XML 结构（Synfig 1.4.5 的已知致命点）"
for a in idle walk run wave jump; do
    grep -q "<nowarp" "$TMP/$a.sif" && bad "$a.sif 含 <nowarp>（会让 Synfig 拒绝加载整个文件）"
done
grep -q 'name="outline_grow"' "$TMP/walk.sif" &&
grep -A1 'name="outline_grow"' "$TMP/walk.sif" | grep -q "<bool" \
    && bad "outline_grow 被写成 <bool>（Synfig 要 <real>）" \
    || ok "outline_grow 是 <real>，且无 <nowarp>"
grep -q '<animated type="angle"' "$TMP/walk.sif" \
    && ok "角度用的是 animated waypoint（静态值会让动画整体失效）" \
    || bad "walk.sif 里没有 animated angle，关键帧不会起作用"

# ------------------------------------------------------- 4. Synfig 能否加载出图
echo "[4/5] Synfig 加载与逐帧差异"
if [ -n "$SYNFIG" ]; then
    for a in idle walk run wave jump; do
        if "$SYNFIG" -q -i "$TMP/$a.sif" -o "$TMP/chk_$a.png" -t png --time 6f -a 2 >/dev/null 2>&1 \
           && [ -s "$TMP/chk_$a.png" ]; then
            ok "$a.sif 可被 Synfig 加载并出图"
        else
            bad "$a.sif Synfig 加载失败 ← 最要紧的一项"
        fi
    done
    "$SYNFIG" -q -i "$TMP/walk.sif" -o "$TMP/w0.png" -t png --time 0f  -a 2 >/dev/null 2>&1
    "$SYNFIG" -q -i "$TMP/walk.sif" -o "$TMP/w6.png" -t png --time 6f  -a 2 >/dev/null 2>&1
    "$SYNFIG" -q -i "$TMP/walk.sif" -o "$TMP/w12.png" -t png --time 12f -a 2 >/dev/null 2>&1
    if [ -f "$TMP/w0.png" ] && ! cmp -s "$TMP/w0.png" "$TMP/w6.png" && ! cmp -s "$TMP/w6.png" "$TMP/w12.png"; then
        ok "walk 的 0/6/12 帧互不相同（动画在动）"
    else
        bad "walk 有若干帧完全相同 → 关键帧没生效或该动作本身是静止相位"
    fi
    # 自绘画布与 Synfig 渲染同源，这里只验证 GUI 侧模型能算出同样的姿态
    $PY - <<'PY' 2>/dev/null && ok "模型侧 frame 0 并腿 / frame 6 分腿" || bad "模型侧腿部相位不对"
from agentcartoon.core import actions, model
rig = model.SkeletonRig()
clip = actions.build("walk", rig=rig)
a0, a6 = clip.pose_at(rig, 0), clip.pose_at(rig, 6)
assert abs(a0["L_leg"] - a0["R_leg"]) < 1e-6, a0
assert abs(a6["L_leg"] - a6["R_leg"]) > 30, a6
PY
else
    for a in idle walk run wave jump; do skip "$a.sif 的 Synfig 加载验证（无 synfig）"; done
    skip "逐帧差异验证（无 synfig）"
fi

# ------------------------------------------------------------ 5. 回读与往返
echo "[5/5] 内嵌元数据回读"
rt="$($PY - "$TMP" <<'PY'
import sys
from pathlib import Path
from agentcartoon.core import sif
tmp = Path(sys.argv[1])
bad = 0
for f in sorted(tmp.glob("*.sif")):
    try:
        d = sif.SifDocument.load(f)
    except Exception as exc:
        print(f"  {f.name} 回读异常: {exc}"); bad += 1; continue
    if len(d.rig.bones) != 15:
        print(f"  {f.name} 回读骨骼数 {len(d.rig.bones)} ≠ 15"); bad += 1
    elif not d.clip.sorted_keyframes():
        print(f"  {f.name} 回读不到关键帧"); bad += 1
print("ROUNDTRIP_OK" if not bad else "ROUNDTRIP_BAD")
PY
)"
if grep -q "^ROUNDTRIP_OK$" <<<"$rt"; then
    ok "全部 .sif 精确回读（15 骨 + 关键帧）"
else
    bad "回读失败" "$(grep -v '^ROUNDTRIP' <<<"$rt" | sed 's/^ *//;s/$/ /' | tr '\n' ';')"
fi

echo
echo "================  $pass 通过 / $fail 失败 / $skip 跳过  ================"
if [ "$fail" -gt 0 ]; then
    echo "有失败项。.sif 合法性（第 3、4 组）优先修，那是 Synfig 能否打开文件的门槛。"
    exit 1
fi
echo "全部通过。注意：本脚本不覆盖 GUI 真实鼠标交互与 Synfig Studio 编辑后回读。"
