---
name: look-lora
description: 自分で学習したルックLoRA（Z-Image Turbo）で画像を塗るツール。「look-lora」「ルックLoRA」で発動。t2i/img2img のみ課金・--go 無しは見積だけ。課金前にユーザー承認。
---

# look-lora — 自分のルックで画像を塗る

## できること／発動キーワード
自分のレタッチ済みスチルから学習した **ルックLoRA（Z-Image Turbo）** で、①ショット文だけから統一ルックの1枚を描く、②既存の画像・動画のフレームにルックを塗り直す。**色はLoRAに学ばせず、カラーグレードで決める**。出力は PNG まで。発動キーワード: 「look-lora」「ルックLoRA」

## 使い方（2通り・`--go` 無し＝dry-run 見積のみ）
```
# (A) ショット文から1枚描く（t2i＋LoRA・1024x576・8step）。ルック語（彩度・コントラスト・ヘイズ・柔光）は書かない＝LoRAの仕事
python3 scripts/lookshot.py frames --shots shots.json --out <出力dir> [--seeds 1234,5678] [--g0] [--go]
# (B) 既存フレームに塗る（img2img denoise 0.45＋LoRA）
python3 scripts/lookshot.py frames --shots shots.json --out <出力dir> --src-frames <dir> --go
```
出力: `frames/`（png・prompts.json・gen_log.jsonl）。**出力はPNGを返すまで**。既存出力はスキップ＝再開可。

## 動画化について（本スキルの対象外）
1. 外部の LoRA を通した画像は来歴認証が切れ、人物が写っていると一部の動画APIで拒否される。素材選定では回避できない構造的な制約
2. 実運用のマルチショット動画はショット文＋プリビズ参照で組むもので、開始・終端の2枚では置き換えられない
3. **塗った画像を動画へ戻す工程は本スキルの対象外**。`i2v`/`post`/`all` は削除していないが既定で停止し、意図して使う場合のみ `--force-video` で動く

## 課金の目安と承認
- t2i／img2img: **$0.005/枚**（0.59MP × $0.0085/MP）。10ショット×2シード＝20枚 ≒$0.10
- **課金前に見積（dry-run）を提示して承認を得る**。`--max-usd`（既定10）超は --go でも止まる
- キー: 環境変数 `FAL_KEY`、無ければ `.env`（`LOOKLORA_ENV_FILE` で場所指定）。値は出力しない

## 既定値
| 項目 | 値 |
|---|---|
| LoRA | `lora_urls.json` のキー（既定 `v2_3000`）・強度 **0.7**・末尾 `, ykflat look` |
| 画像 seed | 1234（`--seeds 1234,5678` で2シード） |
| 副経路 img2img | denoise **0.45**・LoRA 0.7 |
| 対照 G0 | `--g0`＝同一文・LoRA 0（文は変えない） |

## 効きにくい域・注意
- **曇天・霧・夕景の低彩度ショット**は LoRA の差が小さい。数値で切らず実見で採否を決める
- 教師画像の風景が別ショットに混入することがある→被写体・衣装・看板をプロンプトで明示する

## LoRA の差し替え
新しい重み（`.safetensors`）を `--lora <path>` で一度渡すだけ。fal storage へ上がり `scripts/lora_urls.json` にキー登録され、以後はキー名で呼べる。**重み自体はこのリポジトリに含めない**。
