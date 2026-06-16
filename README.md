# OggLooper

## 概要
- 音楽データなどに使われるogg形式のファイルに、ループタグを設定し、対応しているソフトでループできるようにします。
- ループタグ設定はある程度自動で可能な他、不本意な設定をされた場合も調整が可能です。
- オマケとしてmp3やwavをogg変換する機能、必要なライブラリ類を（ユーザーの許可の上で）導入する機能もあります。
- まずはOggLooper.batを実行してください。

## Description

A simple Windows GUI for adding seamless-loop tags (`LOOPSTART` / `LOOPLENGTH`)
to `.ogg` files, powered by
[CrossLooper](https://github.com/Splendide-Imaginarius/crosslooper).
It can also convert mp3/wav to ogg, audition loops, and — when the auto-detected
loop point sounds wrong — let you pick a better loop point from several
candidates by ear.

> **Note:** The detailed documentation below is written in Japanese.

[CrossLooper](https://github.com/Splendide-Imaginarius/crosslooper) を使って
ogg にループタグ（`LOOPSTART` / `LOOPLENGTH`）を付けるための、シンプルな Windows 向け GUI です。
mp3/wav → ogg 変換、ループ試聴、そして自動推定が外れたときに**候補の中からループ地点を選び直す**機能も備えています。

---

## 主な機能

- **ループタグ自動付与** … CrossLooper の相互相関でループ地点を推定し、ogg にタグを書き込み。
- **mp3 / wav → ogg 変換** … 同梱 ffmpeg（libvorbis）で変換。
- **ループ試聴** … 最初から／ループ直前から再生。
- **ループ地点の選び直し（調整タブ）** … 候補を複数提示し、継ぎ目試聴・連続ループ再生で確認して採用。

## 動作環境

- Windows 11（おそらく 10 でも動作）
- Python 3.x … [python.org](https://www.python.org/downloads/windows/) からインストールし、**「Add python.exe to PATH」にチェック**
  - Python が未導入の場合、`OggLooper.bat` 起動時に「winget で公式版を入れますか？」と確認したうえでインストールできます（同意した場合のみ実行。winget が無い環境では手動インストールの案内を表示）。

その他のライブラリと ffmpeg はアプリの「準備」ボタンが自動で導入します（→ [「準備」が行うこと](#準備ボタンが行うこと)）。

## 入手

[リポジトリ](https://github.com/bigdogbox/ogglooper)の「Code」→「Download ZIP」で一式をダウンロードして展開するか、`git clone` してください。

```
git clone https://github.com/bigdogbox/ogglooper.git
```

## 起動

展開したフォルダ内の `OggLooper.bat` をダブルクリック。起動しない場合は `OggLooper_debug.bat` を実行するとエラー内容が表示されます。

画面は「基本」「調整」の2タブ構成です。

## 使い方（基本タブ）

1. **準備** … 初回のみ。必要な Python ライブラリと ffmpeg を自動セットアップします（数分かかることがあります）。
2. **mp3 / wav → ogg 変換**（任意）… 「参照...」でファイルを選び（複数可）、品質を選んで「oggに変換」。元と同じ場所に同名の `.ogg` ができます（同名があれば `_2.ogg` のように退避）。「変換後に下のoggへ自動セット」をオンにすると、変換した ogg がそのまま作業対象になります。変換は一方通行です。
3. **参照...** … ループタグを付けたい `.ogg` を選択。現在のループ情報が表示されます。
4. **ループタグ追加** … CrossLooper がループ地点を推定し、ファイルに直接タグを書き込みます。
   - **既存タグを上書き** … すでにタグがあっても再計算（オフだとスキップ）。
   - **最小ループ長(秒)** … 推定が外れるときのヒント（`--loop-len-min`）。空欄でOK。
5. **再生** / **ループ直前から再生** / **停止** … 耳で確認。

## 使い方（調整タブ）

ループの繋ぎに違和感がある時、ループ地点を**候補の中から選び直す**ためのタブです。CrossLooper は「いちばん一致する1点」だけを選びますが、曲によっては別の地点の方が自然なことがあります。そこで、同じ相関計算から一致度の高い候補を複数提示し、耳で選べます。

1. 基本タブで対象の ogg を選んでおく。
2. **候補を解析** … 相関のピークを信頼度順に一覧表示。
   - **絞り込み（任意）**: ループ開始のおおよその位置が分かるなら「開始 最小/最大(秒)」で探索範囲を絞れます。「最小ループ長(秒)」「候補数」も指定可。すべて空欄なら自動。
3. 候補を選び、**継ぎ目を試聴**（終端手前→開始へジャンプ）や **連続ループ再生** で確認。
4. 良ければ **この候補を採用**。書き込み前に元oggを `元の名前_bk_日時.ogg` として自動バックアップします。一覧の `★` は現在適用中のループ開始に近い候補の目印です。

> 補足: CrossLooper のループ判定は「ループ開始地点の波形が後方（終端付近）で再び現れる」ことを根拠にします。狙った地点の内容が曲の後半で再出現しない場合は候補に上がりにくいことがあります。その時は「開始 最大(秒)」で探索範囲を狙いの側へ寄せてみてください。

## 「準備」ボタンが行うこと

初回セットアップ（「準備」ボタン）は次の処理を行います。**インターネットへアクセスします**ので、内容を確認のうえ実行してください。

- `pip` で次のパッケージをインストール: numpy, scipy, matplotlib, mutagen, tqdm, imageio-ffmpeg, soundfile, sounddevice
- `imageio-ffmpeg` 同梱の ffmpeg 実行ファイルを `bin\ffmpeg.exe` へ配置

手動で入れたい場合は `pip install -r requirements.txt` の後、ffmpeg を PATH に通すか `bin\ffmpeg.exe` を用意してください。

## 注意

- ループ地点は**推定**です。必ず試聴して確認してください。
- タグはファイルに**直接上書き**されます。基本タブで付与する場合はバックアップは作りません（調整タブの「採用」は自動バックアップします）。大事な音源は事前にコピーを。

## 構成ファイル

- `ogglooper.py` … 本体（GUI）
- `loop_candidates.py` … 調整タブ用。CrossLooper の相関計算でループ候補を複数抽出
- `crosslooper.py` … CrossLooper 本体（同梱・改変あり / GPL-3.0）
- `OggLooper.bat` / `OggLooper_debug.bat` … 起動用 / 診断用
- `requirements.txt` / `LICENSE`
- `bin\ffmpeg.exe` … 「準備」時に自動配置（リポジトリには含めません）

## ライセンス

本プロジェクトは **GNU General Public License v3.0 (or later)** で配布されます。全文は [`LICENSE`](LICENSE) を参照。

同梱の `crosslooper.py` が GPL-3.0 であるため、それを利用・同梱する本プロジェクト全体も GPL-3.0 になります。`crosslooper.py` には改変点（matplotlib バックエンドの変更ほか）をファイル冒頭に明記しています。

## クレジット

- **CrossLooper** — Copyright (C) 2023–2024 Splendide Imaginarius. GPL-3.0.
  https://github.com/Splendide-Imaginarius/crosslooper
  （CrossLooper は [syncstart](https://github.com/rpuntaie/syncstart), (C) 2021 Roland Puntaier の改変フォークです）
- 依存ライブラリ: mutagen, numpy, scipy, matplotlib, tqdm, soundfile, sounddevice, imageio-ffmpeg
- 音声処理: [FFmpeg](https://ffmpeg.org/)（imageio-ffmpeg 経由で取得）

## 免責

本ソフトウェアは現状のまま提供され、いかなる保証もありません。利用は自己責任で。
