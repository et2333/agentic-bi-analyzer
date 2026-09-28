"""
重放 BAT 报告生成步骤（使用已有的 state.json 产物）。

用法：
  python scripts/replay_bat_report.py
  python scripts/replay_bat_report.py --state artifacts/workspace/2026-05-12-16-10/reports/2026-05-12_161822_state.json
  python scripts/replay_bat_report.py --state <path/to/state.json> --out <output_dir>

作用：
  加载已有流水线运行的 state.json，直接调用 BAT 报告生成函数（_draft_business_report_markdown
  + _number_figure_captions + _apply_quarto_frontmatter），把结果写到 <run_dir>/reports/
  business_insight_report.md。不需要 LangGraph 图，不需要 RAG，直接复用 state 里已有的
  eda_charts / best_model / eda_report / bat_rag_buckets 等字段。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _find_latest_state_json() -> Path:
    """artifacts/workspace 下按修改时间排序，找最新的 *_state.json。"""
    workspace = PROJECT_ROOT / "artifacts" / "workspace"
    candidates = sorted(workspace.rglob("*_state.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"在 {workspace} 下未找到任何 *_state.json，请用 --state 指定路径。")
    return candidates[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="用已有 state.json 重放 BAT 报告生成")
    parser.add_argument(
        "--state",
        default="",
        metavar="PATH",
        help="state.json 路径；省略则自动找 artifacts/workspace/ 下最新的一个",
    )
    parser.add_argument(
        "--out",
        default="",
        metavar="DIR",
        help="报告输出目录；省略则使用 state.json 里的 run_dir/reports/",
    )
    args = parser.parse_args()

    state_path = Path(args.state).resolve() if args.state else _find_latest_state_json()
    print(f"[replay_bat] 加载 state: {state_path}")

    with open(state_path, encoding="utf-8") as f:
        state: dict = json.load(f)

    # messages 字段在 state.json 里不存在（已被 save_state_snapshot 过滤掉），
    # BAT 生成函数不需要它，补一个空列表避免 KeyError。
    state.setdefault("messages", [])

    from agent.business_translator_agent import (
        _draft_business_report_markdown,
        _quarto_render_if_available,
        _resolve_chart_rel_paths,
    )

    print("[replay_bat] 开始生成报告（可能需要 30-90 秒，取决于 LLM 响应）...")
    report = _draft_business_report_markdown(state)

    # 确定输出目录
    out_dir: Path
    if args.out:
        out_dir = Path(args.out).resolve()
    else:
        run_dir = state.get("run_dir", "")
        if run_dir:
            candidate = Path(run_dir) / "reports"
            # state.json 可能来自其他机器（不同盘符/挂载点），先确认根路径可访问
            root_accessible = Path(candidate.anchor).exists() if candidate.anchor else False
            if root_accessible:
                out_dir = candidate
            else:
                # 回退：把报告写到 state.json 旁边，跨机器也能工作
                out_dir = state_path.parent
                print(f"[replay_bat] run_dir 所在盘符不可访问（{candidate.anchor}），"
                      f"回退输出目录：{out_dir}")
        else:
            out_dir = PROJECT_ROOT / "artifacts" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "business_insight_report.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"[replay_bat] 报告已保存 → {out_path}")

    # 简单摘要
    import re
    lines = report.splitlines()
    yaml_ok = lines[0].strip() == "---"
    fig_labels = re.findall(r"\{#fig-(\d+)\}", report)
    h2_sections = [l for l in lines if l.startswith("## ")]
    charts_found = _resolve_chart_rel_paths(state)

    print()
    print("=== 验证摘要 ===")
    print(f"  YAML front matter 存在：{'[OK]' if yaml_ok else '[MISSING]'}")
    print(f"  Quarto 图片标签：{{#fig-N}} 共 {len(fig_labels)} 个 → {fig_labels[:5]}")
    print(f"  顶层节标题 (##) 数量：{len(h2_sections)}")
    print(f"  state 中 eda_charts 数量：{len(charts_found)}")
    print(f"  报告字数（字符）：{len(report)}")
    print(f"  输出文件：{out_path}")

    # 自动 quarto render（如已安装）
    _quarto_render_if_available(out_path)
    html_path = out_path.with_suffix(".html")
    if html_path.exists():
        print(f"  HTML 输出：{html_path}")
    else:
        print("  （未检测到 Quarto，跳过 HTML 渲染；可手动运行：quarto render <md_path>）")


if __name__ == "__main__":
    main()
