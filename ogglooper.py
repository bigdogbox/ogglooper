#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# OggLooper - GUI for adding loop tags to ogg files via CrossLooper.
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
OggLooper - CrossLooper を使った ogg ループタグ用 GUI

[基本] タブ
- 「準備」 : 動作に必要な Python ライブラリと ffmpeg を自動セットアップ
- mp3/wav → ogg 変換
- 「ループタグ追加」 : CrossLooper でループタグ(LOOPSTART/LOOPLENGTH)を付与
- ループ情報表示 / 再生 / ループ直前から再生

[調整] タブ
- ループ開始の候補を複数(相互相関のピーク)抽出して一覧表示
- 各候補を「継ぎ目試聴」「連続ループ再生」で確認し、良いものを採用
- 採用前に元ogg を自動バックアップ

CrossLooper (c) Splendide Imaginarius / GPL-3.0
https://github.com/Splendide-Imaginarius/crosslooper
"""

import os
import sys
import json
import datetime
import threading
import subprocess
import traceback

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(APP_DIR, "bin")
CROSSLOOPER_PY = os.path.join(APP_DIR, "crosslooper.py")
CANDIDATES_PY = os.path.join(APP_DIR, "loop_candidates.py")
PLAY_LEAD_SECONDS = 10.0   # 「ループ直前から再生」のリード秒数
SEAM_PRE = 2.5             # 継ぎ目試聴: 終端の手前秒数
SEAM_POST = 2.5            # 継ぎ目試聴: 開始からの秒数

# 準備で導入する pip パッケージ
PIP_PACKAGES = [
    "numpy", "scipy", "matplotlib", "mutagen", "tqdm",
    "imageio-ffmpeg", "soundfile", "sounddevice",
]

# 変換品質ラベル -> libvorbis -q:a 値
QUALITY_MAP = {
    "高音質 (q8 / 約256k)": "8",
    "標準 (q6 / 約192k)": "6",
    "小サイズ (q4 / 約128k)": "4",
}


def child_env():
    """ffmpeg をバンドルした bin を PATH 先頭に追加した環境変数。"""
    env = os.environ.copy()
    env["PATH"] = BIN_DIR + os.pathsep + env.get("PATH", "")
    return env


def ffmpeg_exe():
    """同梱 ffmpeg を優先。なければ PATH 上の ffmpeg。"""
    p = os.path.join(BIN_DIR, "ffmpeg.exe")
    return p if os.path.isfile(p) else "ffmpeg"


def unique_ogg_path(src):
    """src と同じ場所・同じ名前の .ogg を返す。既存なら _2, _3... を付与。"""
    base = os.path.splitext(src)[0]
    cand = base + ".ogg"
    i = 2
    while os.path.exists(cand):
        cand = "%s_%d.ogg" % (base, i)
        i += 1
    return cand


def first_tag(tags, name):
    v = tags.get(name.upper())
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return v[0] if v else None
    return v


def fmt_time(seconds):
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    m = int(seconds // 60)
    s = seconds - m * 60
    return "%d:%05.2f" % (m, s)


class LoopPlayer:
    """sounddevice の OutputStream で [start, end) を無限ループ再生する。"""

    def __init__(self):
        self.stream = None

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def play(self, data, sr, start, end, init_pos=None):
        import sounddevice as sd
        self.stop()
        start = int(start)
        end = int(end)
        if end <= start or end > len(data):
            end = len(data)
        pos = int(init_pos) if init_pos is not None else start
        if pos < start or pos >= end:
            pos = start
        ch = data.shape[1] if data.ndim > 1 else 1
        state = {"pos": pos}

        def cb(outdata, frames, time_info, status):
            p = state["pos"]
            n = 0
            while n < frames:
                chunk = min(frames - n, end - p)
                outdata[n:n + chunk] = data[p:p + chunk]
                p += chunk
                n += chunk
                if p >= end:
                    p = start
            state["pos"] = p

        self.stream = sd.OutputStream(samplerate=sr, channels=ch,
                                      dtype="float32", callback=cb)
        self.stream.start()


class App:
    def __init__(self, root):
        self.root = root
        root.title("OggLooper - CrossLooper GUI")
        root.geometry("880x780")
        root.minsize(780, 660)

        self.ogg_path = tk.StringVar()
        self.loop_force = tk.BooleanVar(value=True)
        self.loop_len_min = tk.StringVar(value="")
        self.conv_src = tk.StringVar()
        self.conv_quality = tk.StringVar(value="標準 (q6 / 約192k)")
        self.conv_autoset = tk.BooleanVar(value=True)
        self._conv_files = []

        # 調整タブ
        self.adj_start_min = tk.StringVar(value="")
        self.adj_start_max = tk.StringVar(value="")
        self.adj_len_min = tk.StringVar(value="")
        self.adj_ncand = tk.StringVar(value="8")
        self._candidates = []

        self._busy = False
        self._loop_start_sample = None   # 現在適用中の LOOPSTART
        self._file_sr = None
        self._audio_cache = None         # (path, data(2D float32), sr)
        self._loop_player = LoopPlayer()

        self._build_ui()
        self._refresh_play_buttons()
        self._refresh_adjust_buttons()

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        nb = ttk.Notebook(self.root)
        nb.pack(side="top", fill="both", expand=True, padx=8, pady=(8, 0))
        self.tab_basic = ttk.Frame(nb)
        self.tab_adjust = ttk.Frame(nb)
        nb.add(self.tab_basic, text="基本")
        nb.add(self.tab_adjust, text="調整")
        self._build_basic(self.tab_basic, pad)
        self._build_adjust(self.tab_adjust, pad)

        # --- ステータス(最下部) ---
        self.status = tk.StringVar(
            value="準備ができていない場合はまず「準備」を押してください。")
        ttk.Label(self.root, textvariable=self.status, relief="sunken",
                  anchor="w").pack(side="bottom", fill="x")

        # --- ログ(共有) ---
        frm_log = ttk.LabelFrame(self.root, text="ログ")
        frm_log.pack(side="bottom", fill="x", padx=8, pady=4)
        self.log = tk.Text(frm_log, height=8, wrap="word",
                           state="disabled", font=("Consolas", 9))
        self.log.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        sb = ttk.Scrollbar(frm_log, command=self.log.yview)
        sb.pack(side="right", fill="y", pady=6, padx=(0, 6))
        self.log.config(yscrollcommand=sb.set)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_basic(self, parent, pad):
        # --- mp3/wav -> ogg 変換 ---
        frm_conv = ttk.LabelFrame(parent, text="mp3 / wav → ogg 変換")
        frm_conv.pack(fill="x", **pad)
        row1 = ttk.Frame(frm_conv)
        row1.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Entry(row1, textvariable=self.conv_src).pack(
            side="left", fill="x", expand=True, padx=6, pady=4)
        ttk.Button(row1, text="参照...", command=self.browse_convert).pack(
            side="left", padx=6, pady=4)
        row2 = ttk.Frame(frm_conv)
        row2.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Label(row2, text="品質:").pack(side="left", padx=(6, 2))
        ttk.Combobox(row2, textvariable=self.conv_quality, width=20,
                     state="readonly",
                     values=list(QUALITY_MAP.keys())).pack(side="left")
        ttk.Checkbutton(row2, text="変換後に下のoggへ自動セット",
                        variable=self.conv_autoset).pack(side="left", padx=10)
        self.btn_conv = ttk.Button(row2, text="oggに変換",
                                   command=self.on_convert)
        self.btn_conv.pack(side="left", padx=4)

        # --- ファイル選択 ---
        frm_file = ttk.LabelFrame(parent, text="oggファイル")
        frm_file.pack(fill="x", **pad)
        ent = ttk.Entry(frm_file, textvariable=self.ogg_path)
        ent.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        ttk.Button(frm_file, text="参照...", command=self.browse).pack(
            side="left", padx=6, pady=6)

        # --- 操作ボタン ---
        frm_act = ttk.Frame(parent)
        frm_act.pack(fill="x", **pad)
        self.btn_setup = ttk.Button(frm_act, text="準備", command=self.on_setup)
        self.btn_setup.pack(side="left", padx=4)
        self.btn_add = ttk.Button(frm_act, text="ループタグ追加",
                                  command=self.on_add_tags)
        self.btn_add.pack(side="left", padx=4)
        ttk.Checkbutton(frm_act, text="既存タグを上書き",
                        variable=self.loop_force).pack(side="left", padx=8)
        ttk.Label(frm_act, text="最小ループ長(秒):").pack(side="left")
        ttk.Entry(frm_act, textvariable=self.loop_len_min, width=7).pack(
            side="left", padx=2)

        # --- 再生ボタン ---
        frm_play = ttk.Frame(parent)
        frm_play.pack(fill="x", **pad)
        self.btn_play = ttk.Button(frm_play, text="再生",
                                   command=self.on_play_start)
        self.btn_play.pack(side="left", padx=4)
        self.btn_play_loop = ttk.Button(
            frm_play, text="ループ直前から再生", command=self.on_play_loop)
        self.btn_play_loop.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(frm_play, text="停止", command=self.on_stop)
        self.btn_stop.pack(side="left", padx=4)
        ttk.Button(frm_play, text="情報を更新",
                   command=self.refresh_info).pack(side="right", padx=4)

        # --- ループ情報 ---
        frm_info = ttk.LabelFrame(parent, text="ループ情報")
        frm_info.pack(fill="both", expand=True, **pad)
        self.info = tk.Text(frm_info, height=8, wrap="word",
                            state="disabled", font=("Consolas", 10))
        self.info.pack(fill="both", expand=True, padx=6, pady=6)

    def _build_adjust(self, parent, pad):
        intro = ("ループの繋ぎに違和感がある時に、複数の候補から選び直すためのタブです。"
                 "試聴して良いものを選び「採用」してください。")
        ttk.Label(parent, text=intro, wraplength=820,
                  foreground="#444").pack(fill="x", padx=10, pady=(8, 2))

        # --- 絞り込み(任意) ---
        frm_opt = ttk.LabelFrame(parent, text="候補の絞り込み(任意。空欄で自動)")
        frm_opt.pack(fill="x", **pad)
        r = ttk.Frame(frm_opt)
        r.pack(fill="x", padx=6, pady=6)
        ttk.Label(r, text="開始 最小(秒):").pack(side="left")
        ttk.Entry(r, textvariable=self.adj_start_min, width=7).pack(
            side="left", padx=(2, 10))
        ttk.Label(r, text="開始 最大(秒):").pack(side="left")
        ttk.Entry(r, textvariable=self.adj_start_max, width=7).pack(
            side="left", padx=(2, 10))
        ttk.Label(r, text="最小ループ長(秒):").pack(side="left")
        ttk.Entry(r, textvariable=self.adj_len_min, width=7).pack(
            side="left", padx=(2, 10))
        ttk.Label(r, text="候補数:").pack(side="left")
        ttk.Entry(r, textvariable=self.adj_ncand, width=4).pack(
            side="left", padx=2)
        self.btn_analyze = ttk.Button(r, text="候補を解析",
                                      command=self.on_analyze)
        self.btn_analyze.pack(side="right", padx=4)

        # --- 候補一覧 ---
        frm_tree = ttk.LabelFrame(parent, text="候補一覧 (★ = 現在適用中に近い)")
        frm_tree.pack(fill="both", expand=True, **pad)
        cols = ("no", "start", "end", "length", "conf")
        self.adj_tree = ttk.Treeview(frm_tree, columns=cols, show="headings",
                                     height=8, selectmode="browse")
        heads = {"no": ("#", 50), "start": ("ループ開始", 140),
                 "end": ("ループ終端", 140), "length": ("ループ長", 140),
                 "conf": ("信頼度", 90)}
        for c in cols:
            txt, w = heads[c]
            self.adj_tree.heading(c, text=txt)
            self.adj_tree.column(c, width=w,
                                 anchor=("w" if c == "no" else "center"))
        self.adj_tree.pack(side="left", fill="both", expand=True,
                           padx=(6, 0), pady=6)
        tsb = ttk.Scrollbar(frm_tree, command=self.adj_tree.yview)
        tsb.pack(side="right", fill="y", pady=6, padx=(0, 6))
        self.adj_tree.config(yscrollcommand=tsb.set)
        self.adj_tree.bind("<<TreeviewSelect>>",
                           lambda e: self._refresh_adjust_buttons())

        # --- 試聴 & 採用 ---
        frm_aud = ttk.Frame(parent)
        frm_aud.pack(fill="x", **pad)
        self.btn_seam = ttk.Button(frm_aud, text="継ぎ目を試聴",
                                   command=self.on_seam)
        self.btn_seam.pack(side="left", padx=4)
        self.btn_cont = ttk.Button(frm_aud, text="連続ループ再生",
                                   command=self.on_cont)
        self.btn_cont.pack(side="left", padx=4)
        self.btn_astop = ttk.Button(frm_aud, text="停止", command=self.on_stop)
        self.btn_astop.pack(side="left", padx=4)
        self.btn_apply = ttk.Button(frm_aud, text="この候補を採用",
                                    command=self.on_apply)
        self.btn_apply.pack(side="right", padx=4)
        ttk.Label(parent,
                  text="※「採用」時は書き込み前に元oggを _bk_日時.ogg として自動バックアップします。",
                  foreground="#777").pack(fill="x", padx=10, pady=(0, 6))

    # ------------------------------------------------------------- helpers
    def log_write(self, text):
        def _do():
            self.log.config(state="normal")
            self.log.insert("end", text + "\n")
            self.log.see("end")
            self.log.config(state="disabled")
        self.root.after(0, _do)

    def set_status(self, text):
        self.root.after(0, lambda: self.status.set(text))

    def set_busy(self, busy):
        self._busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.btn_setup, self.btn_add, self.btn_conv):
            self.root.after(0, lambda b=b, s=state: b.config(state=s))
        self.root.after(0, self._refresh_play_buttons)
        self.root.after(0, self._refresh_adjust_buttons)

    def _refresh_play_buttons(self):
        has_file = bool(self.ogg_path.get())
        has_loop = self._loop_start_sample is not None
        pstate = "normal" if (has_file and not self._busy) else "disabled"
        self.btn_play.config(state=pstate)
        self.btn_stop.config(state="normal" if not self._busy else "disabled")
        self.btn_play_loop.config(
            state="normal" if (has_file and has_loop and not self._busy)
            else "disabled")

    def _refresh_adjust_buttons(self):
        if not hasattr(self, "btn_analyze"):
            return
        has_file = bool(self.ogg_path.get())
        has_sel = bool(self.adj_tree.selection()) and bool(self._candidates)
        busy = self._busy
        self.btn_analyze.config(
            state="normal" if (has_file and not busy) else "disabled")
        sel_state = "normal" if (has_sel and not busy) else "disabled"
        for b in (self.btn_seam, self.btn_cont, self.btn_apply):
            b.config(state=sel_state)
        self.btn_astop.config(state="normal" if not busy else "disabled")

    def browse(self):
        path = filedialog.askopenfilename(
            title="oggファイルを選択",
            filetypes=[("Ogg files", "*.ogg"), ("All files", "*.*")])
        if path:
            self._set_ogg(path)

    def _set_ogg(self, path):
        self.ogg_path.set(path)
        self._audio_cache = None
        self.refresh_info()
        self._refresh_play_buttons()
        self._refresh_adjust_buttons()

    def _get_audio(self):
        """oggを2D float32で読み、キャッシュする。"""
        import soundfile as sf
        p = self.ogg_path.get()
        if self._audio_cache and self._audio_cache[0] == p:
            return self._audio_cache[1], self._audio_cache[2]
        data, sr = sf.read(p, dtype="float32", always_2d=True)
        self._audio_cache = (p, data, sr)
        return data, sr

    # ----------------------------------------------------------- 変換
    def browse_convert(self):
        paths = filedialog.askopenfilenames(
            title="変換する音声ファイルを選択 (複数可)",
            filetypes=[("Audio files", "*.mp3 *.wav"),
                       ("MP3", "*.mp3"), ("WAV", "*.wav"),
                       ("All files", "*.*")])
        if paths:
            self._conv_files = list(paths)
            if len(paths) == 1:
                self.conv_src.set(paths[0])
            else:
                self.conv_src.set("%d 個のファイル" % len(paths))

    def on_convert(self):
        if self._busy:
            return
        files = list(self._conv_files)
        if not files:
            messagebox.showwarning("ファイル未指定",
                                   "変換するmp3/wavを指定してください。")
            return
        q = QUALITY_MAP.get(self.conv_quality.get(), "6")
        self.set_busy(True)
        self.set_status("変換中...")
        threading.Thread(target=self._convert_worker, args=(files, q),
                         daemon=True).start()

    def _convert_worker(self, files, q):
        try:
            ff = ffmpeg_exe()
            if ff == "ffmpeg" and not os.path.isfile(
                    os.path.join(BIN_DIR, "ffmpeg.exe")):
                self.log_write("ffmpeg が見つかりません。先に「準備」を押してください。")
            self.log_write("=== ogg 変換を開始 (品質 -q:a %s) ===" % q)
            ok, last_out = 0, None
            for f in files:
                if not os.path.isfile(f):
                    self.log_write("見つかりません: " + f)
                    continue
                out = unique_ogg_path(f)
                cmd = [ff, "-y", "-i", f, "-vn",
                       "-c:a", "libvorbis", "-q:a", q, out]
                rc = self._run_log(cmd)
                if rc == 0 and os.path.isfile(out):
                    ok += 1
                    last_out = out
                    self.log_write("OK: " + os.path.basename(out))
                else:
                    self.log_write("失敗: " + os.path.basename(f))
            self.log_write("=== 変換完了: %d / %d 成功 ===" % (ok, len(files)))
            self.set_status("変換完了: %d / %d 成功" % (ok, len(files)))
            if self.conv_autoset.get() and ok == 1 and last_out:
                self.root.after(0, lambda p=last_out: self._set_ogg(p))
        except Exception as e:
            self.log_write("エラー: " + str(e))
            self.log_write(traceback.format_exc())
            self.set_status("変換に失敗しました。ログを確認してください。")
            self.root.after(0, lambda e=e: messagebox.showerror("失敗", str(e)))
        finally:
            self.set_busy(False)

    # --------------------------------------------------------------- 準備
    def on_setup(self):
        if self._busy:
            return
        self.set_busy(True)
        self.set_status("準備中... (初回は数分かかることがあります)")
        threading.Thread(target=self._setup_worker, daemon=True).start()

    def _setup_worker(self):
        try:
            os.makedirs(BIN_DIR, exist_ok=True)
            self.log_write("=== 準備を開始します ===")
            self.log_write("pip を更新中...")
            self._run_log([sys.executable, "-m", "pip", "install",
                           "--upgrade", "pip"])
            self.log_write("必要なライブラリをインストール中: "
                           + ", ".join(PIP_PACKAGES))
            rc = self._run_log([sys.executable, "-m", "pip", "install",
                                "--upgrade"] + PIP_PACKAGES)
            if rc != 0:
                self.log_write("通常インストールに失敗。--user で再試行します。")
                rc = self._run_log([sys.executable, "-m", "pip", "install",
                                    "--upgrade", "--user"] + PIP_PACKAGES)
            if rc != 0:
                raise RuntimeError("pip インストールに失敗しました。ログを確認してください。")

            self.log_write("ffmpeg を準備中...")
            import importlib
            importlib.invalidate_caches()
            import shutil
            import imageio_ffmpeg
            src = imageio_ffmpeg.get_ffmpeg_exe()
            dst = os.path.join(BIN_DIR, "ffmpeg.exe")
            shutil.copy2(src, dst)
            self.log_write(f"ffmpeg を配置しました: {dst}")

            import importlib as il
            for mod in ("numpy", "scipy", "matplotlib", "mutagen", "tqdm",
                        "soundfile", "sounddevice"):
                il.import_module(mod)
            self.log_write("ライブラリの読み込み確認 OK")

            self.log_write("=== 準備が完了しました ===")
            self.set_status("準備完了。oggを選んで操作できます。")
            self.root.after(0, lambda: messagebox.showinfo(
                "準備完了", "セットアップが完了しました。"))
        except Exception as e:
            self.log_write("エラー: " + str(e))
            self.log_write(traceback.format_exc())
            self.set_status("準備に失敗しました。ログを確認してください。")
            self.root.after(0, lambda e=e: messagebox.showerror(
                "準備に失敗", str(e)))
        finally:
            self.set_busy(False)

    def _run_log(self, cmd):
        """サブプロセスを実行し、出力をログへ。戻り値 = returncode。"""
        self.log_write("$ " + " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                env=child_env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self.log_write("起動失敗: " + str(e))
            return 1
        for line in proc.stdout:
            self.log_write(line.rstrip())
        proc.wait()
        return proc.returncode

    def _run_capture(self, cmd):
        """サブプロセスを実行し、(returncode, stdout) を返す。stderr はログへ。"""
        self.log_write("$ " + " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                env=child_env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self.log_write("起動失敗: " + str(e))
            return 1, ""
        if proc.stderr:
            for line in proc.stderr.splitlines():
                if line.strip():
                    self.log_write(line.rstrip())
        return proc.returncode, proc.stdout

    # --------------------------------------------------------- ループタグ追加
    def on_add_tags(self):
        if self._busy:
            return
        ogg = self.ogg_path.get().strip()
        if not ogg or not os.path.isfile(ogg):
            messagebox.showwarning("ファイル未指定", "oggファイルを指定してください。")
            return
        self.set_busy(True)
        self.set_status("ループ解析中... (曲の長さにより時間がかかります)")
        threading.Thread(target=self._add_tags_worker, args=(ogg,),
                         daemon=True).start()

    def _add_tags_worker(self, ogg):
        try:
            if not os.path.isfile(os.path.join(BIN_DIR, "ffmpeg.exe")):
                self.log_write("ffmpeg が見つかりません。先に「準備」を押してください。")
            cmd = [sys.executable, CROSSLOOPER_PY, ogg]
            if self.loop_force.get():
                cmd.append("--loop-force")
            llm = self.loop_len_min.get().strip()
            if llm:
                try:
                    float(llm)
                    cmd += ["--loop-len-min", llm]
                except ValueError:
                    self.log_write("最小ループ長は数値で指定してください。無視します。")
            self.log_write("=== ループタグ追加を開始 ===")
            rc = self._run_log(cmd)
            if rc != 0:
                raise RuntimeError(
                    "CrossLooper の実行に失敗しました (終了コード %s)。"
                    "準備が済んでいるか、ffmpeg が利用可能か確認してください。" % rc)
            self.log_write("=== 完了。ループ情報を更新します ===")
            self.set_status("ループタグを追加しました。")
            self.refresh_info()
        except Exception as e:
            self.log_write("エラー: " + str(e))
            self.log_write(traceback.format_exc())
            self.set_status("ループタグ追加に失敗しました。ログを確認してください。")
            self.root.after(0, lambda e=e: messagebox.showerror(
                "失敗", str(e)))
        finally:
            self.set_busy(False)

    # ----------------------------------------------------- 調整: 候補解析
    def on_analyze(self):
        if self._busy:
            return
        ogg = self.ogg_path.get().strip()
        if not ogg or not os.path.isfile(ogg):
            messagebox.showwarning("ファイル未指定", "oggファイルを指定してください。")
            return
        self.set_busy(True)
        self.set_status("候補を解析中... (曲の長さにより時間がかかります)")
        threading.Thread(target=self._analyze_worker, args=(ogg,),
                         daemon=True).start()

    def _analyze_worker(self, ogg):
        try:
            if not os.path.isfile(os.path.join(BIN_DIR, "ffmpeg.exe")):
                self.log_write("ffmpeg が見つかりません。先に「準備」を押してください。")
            cmd = [sys.executable, CANDIDATES_PY, ogg]
            for flag, var in (("--loop-start-min", self.adj_start_min),
                              ("--loop-start-max", self.adj_start_max),
                              ("--loop-len-min", self.adj_len_min)):
                val = var.get().strip()
                if val:
                    try:
                        float(val)
                        cmd += [flag, val]
                    except ValueError:
                        self.log_write("数値で指定してください: " + flag)
            nc = self.adj_ncand.get().strip()
            if nc.isdigit() and int(nc) > 0:
                cmd += ["--max-candidates", nc]
            self.log_write("=== 候補解析を開始 ===")
            rc, out = self._run_capture(cmd)
            if rc != 0 or not out.strip():
                raise RuntimeError(
                    "候補解析に失敗しました (終了コード %s)。"
                    "準備が済んでいるか確認してください。" % rc)
            data = json.loads(out)
            n = len(data.get("candidates", []))
            self.log_write("=== 候補 %d 件を取得しました ===" % n)
            if n == 0:
                self.set_status("候補が見つかりませんでした。絞り込み条件を見直してください。")
            else:
                self.set_status("候補 %d 件。試聴して選んでください。" % n)
            self.root.after(0, lambda d=data: self._fill_candidates(d))
        except Exception as e:
            self.log_write("エラー: " + str(e))
            self.log_write(traceback.format_exc())
            self.set_status("候補解析に失敗しました。ログを確認してください。")
            self.root.after(0, lambda e=e: messagebox.showerror("失敗", str(e)))
        finally:
            self.set_busy(False)

    def _fill_candidates(self, data):
        self._candidates = data.get("candidates", [])
        tv = self.adj_tree
        tv.delete(*tv.get_children())
        cur = self._loop_start_sample
        for i, c in enumerate(self._candidates):
            mark = ""
            if cur is not None and abs(c["start_sample"] - cur) < 3000:
                mark = " ★"
            tv.insert("", "end", iid=str(i), values=(
                "%d%s" % (i + 1, mark),
                fmt_time(c["start_sec"]),
                fmt_time(c["end_sec"]),
                fmt_time(c["length_sec"]),
                "%.0f%%" % c["confidence_rel"]))
        self._refresh_adjust_buttons()

    def _selected_candidate(self):
        sel = self.adj_tree.selection()
        if not sel:
            return None
        try:
            i = int(sel[0])
        except ValueError:
            return None
        if 0 <= i < len(self._candidates):
            return self._candidates[i]
        return None

    def on_seam(self):
        c = self._selected_candidate()
        if not c:
            return
        try:
            import numpy as np
            import sounddevice as sd
            data, sr = self._get_audio()
            st, en = c["start_sample"], c["end_sample"]
            pre, post = int(SEAM_PRE * sr), int(SEAM_POST * sr)
            a = data[max(0, en - pre):en]
            b = data[st:st + post]
            buf = np.concatenate([a, b], axis=0)
            self._loop_player.stop()
            sd.stop()
            sd.play(buf, sr)
            self.set_status("継ぎ目を試聴中（終端の手前→ジャンプ→開始）")
        except Exception as e:
            messagebox.showerror(
                "再生エラー", str(e) + "\n\n再生には準備が必要です。")

    def on_cont(self):
        c = self._selected_candidate()
        if not c:
            return
        try:
            import sounddevice as sd
            data, sr = self._get_audio()
            st, en = c["start_sample"], c["end_sample"]
            init = max(st, en - int(SEAM_PRE * sr))
            sd.stop()
            self._loop_player.play(data, sr, st, en, init_pos=init)
            self.set_status("連続ループ再生中（「停止」で終了）")
        except Exception as e:
            messagebox.showerror(
                "再生エラー", str(e) + "\n\n再生には準備が必要です。")

    def on_apply(self):
        c = self._selected_candidate()
        if not c:
            return
        ogg = self.ogg_path.get().strip()
        if not ogg or not os.path.isfile(ogg):
            messagebox.showwarning("ファイル未指定", "oggファイルを指定してください。")
            return
        msg = ("この候補を採用します。\n\n開始: %s\n終端: %s\nループ長: %s\n\n"
               "元oggをバックアップしてからタグを書き込みます。" % (
                   fmt_time(c["start_sec"]), fmt_time(c["end_sec"]),
                   fmt_time(c["length_sec"])))
        if not messagebox.askokcancel("採用の確認", msg):
            return
        self.set_busy(True)
        self.set_status("採用中...")
        threading.Thread(target=self._apply_worker, args=(ogg, c),
                         daemon=True).start()

    def _apply_worker(self, ogg, c):
        try:
            import shutil
            import mutagen
            ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            stem, ext = os.path.splitext(ogg)
            bk = "%s_bk_%s%s" % (stem, ts, ext or ".ogg")
            shutil.copy2(ogg, bk)
            self.log_write("バックアップ作成: " + os.path.basename(bk))

            mf = mutagen.File(ogg)
            if mf is None or getattr(mf, "tags", None) is None:
                raise RuntimeError("この形式にはタグを書き込めません。")
            mf["LOOPSTART"] = [str(int(c["start_sample"]))]
            mf["LOOPLENGTH"] = [str(int(c["length_sample"]))]
            mf.save()
            self.log_write("採用: LOOPSTART=%d LOOPLENGTH=%d"
                           % (int(c["start_sample"]), int(c["length_sample"])))
            self.set_status("候補を採用しました。基本タブの情報も更新しました。")
            self.root.after(0, self.refresh_info)
            self.root.after(0, self._mark_applied)
        except Exception as e:
            self.log_write("エラー: " + str(e))
            self.log_write(traceback.format_exc())
            self.set_status("採用に失敗しました。ログを確認してください。")
            self.root.after(0, lambda e=e: messagebox.showerror("失敗", str(e)))
        finally:
            self.set_busy(False)

    def _mark_applied(self):
        """採用後、一覧の★表示を更新。"""
        cur = self._loop_start_sample
        for i, c in enumerate(self._candidates):
            mark = ""
            if cur is not None and abs(c["start_sample"] - cur) < 3000:
                mark = " ★"
            if self.adj_tree.exists(str(i)):
                vals = list(self.adj_tree.item(str(i), "values"))
                vals[0] = "%d%s" % (i + 1, mark)
                self.adj_tree.item(str(i), values=vals)

    # ------------------------------------------------------------- 情報表示
    def refresh_info(self):
        """ループ情報を読み取って表示。再生用キャッシュも更新。"""
        ogg = self.ogg_path.get().strip()
        self._loop_start_sample = None
        self._file_sr = None
        lines = []
        if not ogg or not os.path.isfile(ogg):
            self._set_info("oggファイルを指定してください。")
            self._refresh_play_buttons()
            return
        try:
            import mutagen
            sr = frames = channels = None
            try:
                import soundfile as sf
                inf = sf.info(ogg)
                sr, frames, channels = inf.samplerate, inf.frames, inf.channels
            except Exception:
                pass

            mf = mutagen.File(ogg)
            tags = {}
            if mf is not None and getattr(mf, "tags", None) is not None:
                try:
                    for k, v in mf.tags:
                        tags[k.upper()] = v
                except Exception:
                    try:
                        for k in mf.keys():
                            tags[k.upper()] = mf[k][0]
                    except Exception:
                        pass

            lines.append("ファイル: " + os.path.basename(ogg))
            if sr:
                dur = frames / sr if sr else 0
                lines.append("サンプルレート: %d Hz / チャンネル: %s / 長さ: %s (%d サンプル)"
                             % (sr, channels, fmt_time(dur), frames))
            else:
                lines.append("(soundfile 未導入のため長さ等は表示できません。準備を実行してください)")

            ls = first_tag(tags, "LOOPSTART")
            ll = first_tag(tags, "LOOPLENGTH")
            lines.append("")
            if ls is not None and ll is not None:
                try:
                    ls_i = int(float(ls))
                    ll_i = int(float(ll))
                    le_i = ls_i + ll_i
                    self._loop_start_sample = ls_i
                    self._file_sr = sr
                    lines.append("LOOPSTART  : %d サンプル" % ls_i
                                 + (" (%s)" % fmt_time(ls_i / sr) if sr else ""))
                    lines.append("LOOPLENGTH : %d サンプル" % ll_i
                                 + (" (%s)" % fmt_time(ll_i / sr) if sr else ""))
                    lines.append("ループ終端 : %d サンプル" % le_i
                                 + (" (%s)" % fmt_time(le_i / sr) if sr else ""))
                except ValueError:
                    lines.append("LOOPSTART/LOOPLENGTH の値を解釈できません: %s / %s"
                                 % (ls, ll))
            else:
                lines.append("ループタグ: なし (「ループタグ追加」を押してください)")

            ls2 = first_tag(tags, "LOOP_START")
            le2 = first_tag(tags, "LOOP_END")
            if ls2 is not None or le2 is not None:
                lines.append("LOOP_START/LOOP_END (秒): %s / %s" % (ls2, le2))

            self._set_info("\n".join(lines))
        except Exception as e:
            self._set_info("情報の取得に失敗しました: " + str(e)
                           + "\n(準備が済んでいない可能性があります)")
        self._refresh_play_buttons()
        self._refresh_adjust_buttons()

    def _set_info(self, text):
        def _do():
            self.info.config(state="normal")
            self.info.delete("1.0", "end")
            self.info.insert("1.0", text)
            self.info.config(state="disabled")
        self.root.after(0, _do)

    # --------------------------------------------------------------- 再生
    def _load_audio(self):
        import soundfile as sf
        data, sr = sf.read(self.ogg_path.get(), dtype="float32")
        return data, sr

    def on_play_start(self):
        self._play(from_sample=0)

    def on_play_loop(self):
        if self._loop_start_sample is None:
            messagebox.showinfo("情報", "先にループタグを追加してください。")
            return
        self._play(from_sample=None)

    def _play(self, from_sample):
        ogg = self.ogg_path.get().strip()
        if not ogg or not os.path.isfile(ogg):
            messagebox.showwarning("ファイル未指定", "oggファイルを指定してください。")
            return
        try:
            import sounddevice as sd
            data, sr = self._load_audio()
            if from_sample is None:
                start = max(0, int(self._loop_start_sample - PLAY_LEAD_SECONDS * sr))
            else:
                start = max(0, int(from_sample))
            self._loop_player.stop()
            sd.stop()
            sd.play(data[start:], sr)
            self.set_status("再生中... (%s から)" % fmt_time(start / sr))
        except Exception as e:
            messagebox.showerror(
                "再生エラー",
                str(e) + "\n\n再生には準備(soundfile / sounddevice)が必要です。")

    def on_stop(self):
        try:
            import sounddevice as sd
            self._loop_player.stop()
            sd.stop()
            self.set_status("停止しました。")
        except Exception:
            pass

    def on_close(self):
        try:
            import sounddevice as sd
            self._loop_player.stop()
            sd.stop()
        except Exception:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
# end of ogglooper.py
