# look-lora

Paint your own cinematic look onto images with a personal Z-Image LoRA — a small CLI that doubles as a Claude Code skill.
自分で学習したルックLoRA（Z-Image Turbo）で画像を「塗る」ための小さなCLI。Claude Code のスキルとしても使えます。

**Weights are not in the repo — they ship as a [Release](https://github.com/yama-kiyo/look-lora/releases/tag/v1.0.0)** (`zimage_v2_3000.safetensors`, CC BY 4.0). Download it, then run `lookshot.py frames --lora <downloaded .safetensors>` once: the weights are uploaded to fal storage and registered in `scripts/lora_urls.json` automatically.
（重みはリポジトリではなく [Release](https://github.com/yama-kiyo/look-lora/releases/tag/v1.0.0) で配布。DL後に `--lora <落とした.safetensors>` で一度通せば fal へ自動アップロードされ `lora_urls.json` に登録されます）
You can of course train your own LoRA on your own stills instead; this repo ships the runner either way.

## Requirements / 前提
- A [fal.ai](https://fal.ai) account and `FAL_KEY` in your environment (or a `.env`; point at it with `LOOKLORA_ENV_FILE`)
- Python 3.8+ / `pip install fal-client requests Pillow`
- A Z-Image LoRA (`.safetensors`) — grab `zimage_v2_3000.safetensors` from the [Release](https://github.com/yama-kiyo/look-lora/releases/tag/v1.0.0), or train your own

## Install / インストール
```bash
git clone https://github.com/yama-kiyo/look-lora.git
cp scripts/lora_urls.example.json scripts/lora_urls.json   # then fill in / or let --lora register it
```
As a Claude Code skill: copy the whole folder to `~/.claude/skills/look-lora/` and say "look-lora".
単体CLIとしてそのまま `python3 scripts/lookshot.py ...` でも使えます。

## Usage / 使い方
```bash
# (A) text-to-image: one frame per shot (1024x576, 8 steps, LoRA 0.7)
python3 scripts/lookshot.py frames --shots scripts/shots.example.json --out ./out --go

# (B) img2img: repaint existing frames (denoise 0.45 + LoRA)
python3 scripts/lookshot.py frames --shots shots.json --out ./out --src-frames ./src --go

# first run with your own weights: uploads to fal storage and registers the key
python3 scripts/lookshot.py frames --shots shots.json --out ./out --lora ./my_look.safetensors --go
```
**Without `--go` nothing is charged** — you only get a cost estimate (≈ $0.005 per image). `--max-usd` (default 10) is a hard stop.
Don't write look words (saturation, contrast, haze, soft light) into the prompt: that is the LoRA's job. Color grading stays outside the model.

## Scope: images only / 動画化は対象外
Video generation (`i2v` / `post` / `all`) is in the code but disabled by default, because (1) images passed through an external LoRA lose provenance attestation and get rejected by some video APIs when a person is in frame, and (2) a real multi-shot video workflow is built on shot prompts and previz references, which a pair of start/end frames cannot replace. Use `--force-video` only if you know you want it.

## License
MIT © yama-kiyo
