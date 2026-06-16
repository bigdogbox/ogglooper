#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# OggLooper - loop candidate extractor.
# Copyright (C) 2026 bigdogbox
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
loop_candidates.py

CrossLooper の相関エンジン(corrabs / read_normalized)を再利用して、
ループ開始の「候補」を1つだけでなく複数(相互相関の信頼度ピーク)抽出し、
JSON で出力する。OggLooper の「調整」タブから呼ばれる。

  標準出力 : JSON  (候補一覧)
  標準エラー: 進捗ログ

CrossLooper (c) Splendide Imaginarius / GPL-3.0
"""

import sys
import os
import json
import math
import argparse
import pathlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import crosslooper as cl  # noqa: E402  (matplotlib=Agg, ffmpeg を利用)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def main():
    ap = argparse.ArgumentParser(description="ループ候補を複数抽出して JSON 出力")
    ap.add_argument("ogg")
    ap.add_argument("--loop-start-min", type=float, default=5.0)
    ap.add_argument("--loop-start-max", type=float, default=None)
    ap.add_argument("--loop-end-min", type=float, default=20.0)
    ap.add_argument("--loop-len-min", type=float, default=0.0)
    ap.add_argument("--step", type=float, default=0.5,
                    help="探索解像度(秒)。小さいほど精密だが遅い。")
    ap.add_argument("--search-len", type=float, default=5.0)
    ap.add_argument("--max-candidates", type=int, default=8)
    ap.add_argument("--min-rel", type=float, default=5.0,
                    help="相対信頼度がこの%%未満の候補は除外(最低3件は残す)。")
    args = ap.parse_args()

    # CrossLooper のグローバルを通常解析向けに設定
    cl.take = None
    cl.normalize = False
    cl.denoise = False
    cl.lowpass = 0
    cl.verbose = False

    log("デコード中...")
    sr, s1, s2 = cl.read_normalized(pathlib.Path(args.ogg), pathlib.Path(args.ogg))
    n_total = len(s1)

    init_start = int(args.loop_start_min * sr)
    searchlen_samples = int(args.search_len * sr)
    init_end_min = int(args.loop_end_min * sr)

    loopstartmax_samples = math.inf
    if args.loop_start_max is not None:
        loopstartmax_samples = args.loop_start_max * sr
    loopstartmax_samples = int(min(loopstartmax_samples, n_total * 0.47))

    search_offset_max = n_total - searchlen_samples
    search_offset_max = min(search_offset_max, loopstartmax_samples - init_start)
    step_samples = max(1, int(args.step * sr))

    starts, ends, conf = [], [], []
    if search_offset_max <= 0:
        log("探索範囲が不足しています(ファイルが短すぎる/開始範囲が広すぎる)。")
    else:
        total = max(1, search_offset_max)
        next_report = 0
        for off in range(0, search_offset_max, step_samples):
            this_start = init_start + off
            this_end_min = init_end_min + off
            _, _, padsize, xmax, ca = cl.corrabs(
                s1[this_start:][:searchlen_samples], s2[this_end_min:])
            this_ca = ca[xmax]
            norm = searchlen_samples * max(1, (len(s2) - this_end_min))
            nca = this_ca / norm
            this_end = this_end_min + (padsize - xmax)
            this_length = this_end - this_start
            valid = (this_end <= n_total) and \
                    (this_length >= args.loop_len_min * sr) and (this_length > 0)
            starts.append(this_start)
            ends.append(int(this_end))
            conf.append(float(nca) if valid else -1.0)
            if off >= next_report:
                log("解析 %d%%" % int(100 * off / total))
                next_report = off + total // 10

    starts = np.array(starts, dtype=np.int64)
    ends = np.array(ends, dtype=np.int64)
    conf = np.array(conf, dtype=np.float64)

    peaks = []
    if len(conf):
        cpos = np.where(conf > 0, conf, 0.0)
        try:
            from scipy.signal import find_peaks
            distance = max(1, int(round(2.0 / args.step)))
            pk, _ = find_peaks(cpos, distance=distance)
            peaks = list(pk)
        except Exception as e:
            log("find_peaks 失敗、argmax で代替: " + str(e))
        # グローバル最大も必ず候補に含める
        gmax = int(np.argmax(cpos))
        if cpos[gmax] > 0 and gmax not in peaks:
            peaks.append(gmax)
        # 有効のみ、信頼度降順
        peaks = [int(p) for p in peaks if conf[p] > 0]
        peaks = sorted(peaks, key=lambda p: conf[p], reverse=True)
        peaks = peaks[:max(1, args.max_candidates)]

    cmax = max((conf[p] for p in peaks), default=1.0) or 1.0
    all_cands = []
    for p in peaks:
        st = int(starts[p])
        en = int(ends[p])
        ln = en - st
        all_cands.append({
            "start_sample": st,
            "start_sec": st / sr,
            "end_sample": en,
            "end_sec": en / sr,
            "length_sample": ln,
            "length_sec": ln / sr,
            "confidence": float(conf[p]),
            "confidence_rel": float(conf[p] / cmax * 100.0),
        })
    # 弱すぎる(ノイズ)候補を除外。ただし最低3件は残す。
    strong = [c for c in all_cands if c["confidence_rel"] >= args.min_rel]
    candidates = strong if len(strong) >= 3 else all_cands[:3]

    out = {
        "sample_rate": int(sr),
        "num_samples": int(n_total),
        "duration_sec": n_total / sr,
        "candidates": candidates,
    }
    print(json.dumps(out))
    log("候補 %d 件を出力しました。" % len(candidates))


if __name__ == "__main__":
    main()
