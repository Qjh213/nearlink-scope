#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LinkScope 日志判读：把 QCOM 落盘的 CSV 变成 W1 验收表。

用法:
    python tools/analyze_log.py <日志路径> [--json]

做四件事：
  1. 找出预热段（`to`/`dup` 还在涨的那一段），从稳态里剔掉；
  2. 按整段三等分给一致性表（对应"每组 3 次重复"的原始意图）；
  3. 打印分位数、采样率、丢包与 B 端处理耗时；
  4. 直接给出验收结论（能不能当基线用）。
"""
import json
import re
import statistics
import sys

TS = re.compile(r'^\[(\d{4})-(\d\d)-(\d\d)_(\d\d):(\d\d):(\d\d):(\d{3})\]')


def q(vals, p):
    if not vals:
        return None
    v = sorted(vals)
    return v[min(len(v) - 1, max(0, int(round((len(v) - 1) * p))))]


def parse(path):
    rows, sums = [], []
    meta = {"role": None, "link": None, "ready": None, "tunes": [], "grants": []}
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            m = TS.match(line)
            if not m:
                continue
            t = (int(m.group(4)) * 3600 + int(m.group(5)) * 60
                 + int(m.group(6)) + int(m.group(7)) / 1000.0)
            body = line[m.end():].strip()
            if body.startswith('S,'):
                p = body.split(',')
                if len(p) >= 7:
                    try:
                        rows.append((t, int(p[4]), int(p[5])))      # t, rtt, bproc
                    except ValueError:
                        pass
            elif '# SUM' in body:
                d = {}
                for kv in body.split():
                    if '=' not in kv:
                        continue
                    k, v = kv.split('=', 1)
                    try:
                        d[k] = int(v.split('/')[0])                 # bproc=10/19 -> 10
                    except ValueError:
                        pass
                sums.append((t, d))
            elif 'role=' in body:
                meta["role"] = body
            elif 'link=' in body:
                meta["link"] = body
            elif 'link ready' in body:
                meta["ready"] = t
            elif 'tune slot=' in body:
                meta["tunes"].append((t, body))
            elif 'param updated' in body or 'intv=' in body:
                meta["grants"].append((t, body))
    return rows, sums, meta


def summarise(name, seg):
    if not seg:
        return {"window": name, "n": 0}
    r = [x[1] for x in seg]
    b = [x[2] for x in seg]
    return {
        "window": name, "n": len(r),
        "p50": q(r, .5), "p90": q(r, .9), "p99": q(r, .99), "p999": q(r, .999),
        "max": max(r), "mean": round(statistics.mean(r)), "stdev": round(statistics.pstdev(r)),
        "bproc_avg": round(statistics.mean(b)), "bproc_max": max(b),
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = sys.argv[1]
    as_json = '--json' in sys.argv
    rows, sums, meta = parse(path)
    out = {"file": path, "meta": {k: (v if k not in ('tunes', 'grants') else len(v)) for k, v in meta.items()}}

    if not rows:
        print('没找到 S 行：确认这一轮开了 CONFIG_LINK_SCOPE_RAW_PRINT=y')
        return 1

    t0, t1 = rows[0][0], rows[-1][0]
    span = t1 - t0
    out["span_s"] = round(span, 1)
    out["rate"] = round(len(rows) / span, 2) if span > 0 else 0

    # 预热：`to`/`dup` 停止增长的那一刻
    warm_from = t0
    steady_gap = None
    if sums:
        final = sums[-1][1]
        frozen = [(t, d) for t, d in sums
                  if d.get('dup') == final.get('dup') and d.get('to') == final.get('to')]
        if frozen:
            warm_from = frozen[0][0]
            g0 = frozen[0][1].get('sent', 0) - frozen[0][1].get('recv', 0)
            g1 = final.get('sent', 0) - final.get('recv', 0)
            steady_gap = g1 - g0      # 预热之后新丢的包（应该恒为 0）
    out["warmup_s"] = round(warm_from - t0, 1)
    out["sum_first"], out["sum_last"] = (sums[0][1] if sums else {}), (sums[-1][1] if sums else {})

    steady = [x for x in rows if x[0] >= warm_from]
    out["steady"] = summarise('steady', steady)

    thirds = []
    s0 = steady[0][0] if steady else t0
    s1 = steady[-1][0] if steady else t1
    d = s1 - s0
    for i in range(3):
        a, b = s0 + d * i / 3.0, s0 + d * (i + 1) / 3.0
        thirds.append(summarise(f'third{i + 1}', [x for x in steady if a <= x[0] < b]))
    out["thirds"] = thirds

    # 验收
    verdict = []
    if meta["role"] is None or 'initiator' not in (meta["role"] or ''):
        verdict.append('角色不对：日志里没有 role=initiator')
    if meta["ready"] is None:
        verdict.append('没看到 link ready')
    if sums:
        f = sums[-1][1]
        if steady_gap is not None:
            out["steady_gap"] = steady_gap
            if steady_gap > 2:
                verdict.append(f"稳态新丢 {steady_gap} 包（>2）")
        if f.get('bad', 0):
            verdict.append(f"bad_frame={f['bad']}")
    if not verdict:
        verdict.append('PASS')
    out["verdict"] = verdict

    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f'文件: {path}')
    print(f'时长: {out["span_s"]}s  样本: {len(rows)}  速率: {out["rate"]}/s  预热: {out["warmup_s"]}s')
    if meta["role"]:
        print(f'  {meta["role"]}')
    if meta["link"]:
        print(f'  {meta["link"]}')
    if sums:
        print(f'  首条 SUM: {sums[0][1]}')
        print(f'  末条 SUM: {sums[-1][1]}')
    s = out["steady"]
    if s["n"]:
        print(f'\n稳态 n={s["n"]}  p50={s["p50"]}  p90={s["p90"]}  p99={s["p99"]}  '
              f'p999={s["p999"]}  max={s["max"]}  mean={s["mean"]}  stdev={s["stdev"]}  '
              f'bproc={s["bproc_avg"]}/{s["bproc_max"]}')
        print('\n三段一致性:')
        for t in thirds:
            if t["n"]:
                print(f'  {t["window"]}: n={t["n"]:<6} p50={t["p50"]:<8} p99={t["p99"]:<8} max={t["max"]}')
    print('\n验收: ' + ' | '.join(verdict))
    if out.get('steady_gap') is not None:
        print(f'稳态新丢包: {out["steady_gap"]}（预热之后 sent-recv 的增量）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
