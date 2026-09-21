# look-lora

Paint your own cinematic look onto images with a personal Z-Image LoRA — a small CLI that doubles as a Claude Code skill.
自分で学習したルックLoRA（Z-Image Turbo）で画像を「塗る」ための小さなCLI。Claude Code のスキルとしても使えます。

**Trained weights are not included.** You train the LoRA on your own retouched stills; this repo only ships the runner.
（学習済み重みは同梱しません。LoRA はご自身の素材で学習してください）

## Requirements / 前提
- A [fal.ai](https://fal.ai) account and `FAL_KEY` in your environment (or a `.env`; point at it with `LOOKLORA_ENV_FILE`)
- Python 3.8+ / `pip install fal-client requests Pillow`
- Your own Z-Image LoRA (`.safetensors`)

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
