# look-lora Windows セットアップ（PowerShell・無課金）

1. **配置**: リポジトリを clone して、Claude Code のスキルとして使うならフォルダごと `C:\Users\<user>\.claude\skills\look-lora` へコピーする。単体CLIとして使うなら clone した場所のままでよい。
2. **キー**: `FAL_KEY` を環境変数に入れるか、`.env` に書いて場所を指定する。
   `[Environment]::SetEnvironmentVariable("LOOKLORA_ENV_FILE", "<.env のフルパス>", "User")`
   `.env` は `$LOOKLORA_ENV_FILE` → カレントの `.env` → スクリプトの1つ上の `.env` の順で探索される。キーの値は出力・貼付しない。
3. **LoRA**: `scripts\lora_urls.example.json` を `scripts\lora_urls.json` にコピーし、自分の LoRA の fal storage URL を入れる。URL が無ければ手元の `.safetensors` を `--lora "<そのパス>"` で一度渡せば fal へ上がり自動登録される（アップロード自体は無料）。
4. **依存**: `python --version`（3.8+）→ `pip install fal-client requests Pillow`。動画化オプション（既定では停止）を使う場合のみ ffmpeg も必要: `ffmpeg -encoders | Select-String prores_ks`（出なければ gyan.dev の full ビルドを PATH へ）。Windows は `python3` ではなく `python`。
5. **動作確認（課金なし）**:
   `python scripts\lookshot.py frames --shots scripts\shots.example.json --out "$env:TEMP\lookshot_test"`
   見積 `$0.015` が表示されれば可。

## Windows 互換のための実装メモ
- stdout/stderr を UTF-8 に再設定（PowerShell の cp932 で日本語・`≒` が UnicodeEncodeError にならないように）
- `removeprefix` / `removesuffix`（3.9+）は使わず正規表現＝Python 3.8 でも動く
- パスは全て `pathlib`（`\` 混在可）／`.env` は `splitlines()` で CRLF を吸収／ffmpeg は `subprocess` のリスト渡し＝フィルタ文字列の引用符・`:` の問題なし
