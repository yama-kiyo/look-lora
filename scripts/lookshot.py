#!/usr/bin/env python3
"""lookshot — paint your own look onto images with a personal Z-Image LoRA.
（自分で学習したルックLoRAで画像を塗る画像CLI）

  frames : ショット文 → t2i＋LoRA で開始フレーム（1024x576・8step）→ 終端＝中央8%プッシュイン（ローカル処理・無料）
           --src-frames <dir> を渡すと副経路: 既存フレームへ img2img denoise 0.45＋LoRA（既存ショットに寄せる）
           本ツールの役割はここまで（PNG を返して終わり）。

  [既定で停止] i2v / post / all : 動画化（Seedance I2V・グレイン・ProRes化）はコードとしては残っているが
           既定で停止する。意図して使う場合のみ --force-video を付ける。

課金は frames（と、--force-video を付けた i2v）のみ。**`--go` が無ければ必ず dry-run（費用見積のみ・課金なし）**。
キーは環境変数 FAL_KEY / ARK_API_KEY、無ければ .env から読む（`LOOKLORA_ENV_FILE` で場所を指定）。値は一切出力しない。

  python3 lookshot.py frames --shots shots.json --out ./out            # 見積のみ
  python3 lookshot.py frames --shots shots.json --out ./out --go       # 生成（PNG を出力）
"""
import os, sys, re, json, time, glob, hashlib, pathlib, argparse, subprocess
for _st in (sys.stdout, sys.stderr):   # Win PowerShell（cp932）で日本語・記号出力が落ちないように
    if hasattr(_st, "reconfigure"):
        try: _st.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass

HERE = pathlib.Path(__file__).resolve().parent
URLS_FILE = HERE / "lora_urls.json"          # LoRA名 → fal storage URL（新しい重みを --lora <path> で渡すと自動追記）

# ---- 既定値（検証で固めた値。変えるなら自分のLoRAで再検証すること）----
T2I_ENDPOINT = "fal-ai/z-image/turbo/lora"                    # 主経路
I2I_ENDPOINT = "fal-ai/z-image/turbo/image-to-image/lora"     # 副経路
T2I_W, T2I_H, STEPS = 1024, 576, 8
USD_PER_MP = 0.0085                                           # fal z-image 単価
DENOISE = 0.45                                                # img2img 既定
DEFAULT_LORA, DEFAULT_STRENGTH, LOOK_SUFFIX = "v2_3000", 0.7, ", ykflat look"   # LoRA名・強度・トリガー語
CAMERA = "slow dolly in, natural light, no color change"      # 既定カメラ文
I2V_MODEL, I2V_VSEED = "dreamina-seedance-2-5-260628", 11
ARK_BASE = "https://ark.ap-southeast.bytepluses.com/api/v3"
TOK_PER_CLIP, USD_PER_MTOK = 108_900, 7.7                     # 実測（5s/720p）≒ $0.84/本
GRAIN = 4                                                     # 技術グレイン既定


# ---- 環境解決 ----
def env_file():
    """.env の探索順: $LOOKLORA_ENV_FILE → カレントの .env → このスクリプトの1つ上の .env"""
    if os.environ.get("LOOKLORA_ENV_FILE"): return pathlib.Path(os.environ["LOOKLORA_ENV_FILE"]).expanduser()
    for c in (pathlib.Path.cwd() / ".env", HERE.parent / ".env"):
        if c.exists(): return c
    return pathlib.Path(".env")

def load_env(*keys):
    """必要なキーが環境変数に無ければ .env から読む。値はログに出さない。"""
    missing = [k for k in keys if not os.environ.get(k)]
    if not missing: return
    env = env_file()
    if env.exists():
        for line in env.read_text(errors="ignore").splitlines():
            line = re.sub(r"^export\s+", "", line.strip()).strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    still = [k for k in keys if not os.environ.get(k)]
    if still: sys.exit(f"キー未設定: {', '.join(still)}（環境変数か LOOKLORA_ENV_FILE で指定した .env に置いてください）")


# ---- ショット定義 ----
def load_shots(path):
    """shots.json → [(id, prompt_body, camera)]。配列（文字列 or {id,prompt,camera}）／辞書（キー=ID・`_` 始まりのメタキーは除外）どちらも可。
    先頭 'ykflat, ' が付いていれば剥がす（末尾の look語は本CLIが付けるため）。"""
    if not path: return []
    d = json.loads(pathlib.Path(path).read_text())
    items = d.items() if isinstance(d, dict) else enumerate(d, 1)
    out = []
    for k, v in items:
        if isinstance(k, str) and k.startswith("_"): continue
        if isinstance(v, str): sid, body, cam = f"S{int(k):02d}", v, CAMERA
        else: sid, body, cam = v.get("id") or (k if isinstance(k, str) else f"S{int(k):02d}"), v["prompt"], v.get("camera") or CAMERA
        body = re.sub(r"^\s*ykflat\s*,\s*", "", body).strip().rstrip(",")
        out.append((sid, body, cam))
    return out

def shot_id_of(stem):
    """フレーム名 → ショットID（'<id>_s<seed>[_G0]_start' 形式を逆算）"""
    return re.sub(r"(_s\d+)?(_G\d+)?$", "", re.sub(r"_(start|end)$", "", stem))


# ---- 共通ユーティリティ ----
def sha12(p): return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()[:12]

def jload(p, default): return json.loads(pathlib.Path(p).read_text()) if pathlib.Path(p).exists() else default

def jsave(p, d): pathlib.Path(p).parent.mkdir(parents=True, exist_ok=True); pathlib.Path(p).write_text(json.dumps(d, indent=1, ensure_ascii=False))

def log_line(p, rec):
    pathlib.Path(p).parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f: f.write(json.dumps(rec, ensure_ascii=False) + "\n")

def fal_upload(path, cache_file):
    """ローカルファイルを fal storage へ（無料）。sha12 でキャッシュし同一内容は再アップしない。"""
    import fal_client
    cache = jload(cache_file, {}); key = f"{pathlib.Path(path).name}#{sha12(path)}"
    if key not in cache:
        cache[key] = fal_client.upload_file(str(path)); jsave(cache_file, cache)
    return cache[key]

def resolve_lora(spec):
    """--lora: URL ／ lora_urls.json のキー ／ ローカル .safetensors（fal へ上げてキーを自動登録）"""
    urls = jload(URLS_FILE, {})
    if not URLS_FILE.exists():
        print(f"{URLS_FILE.name} が無いので新規作成します（例: {URLS_FILE.with_name('lora_urls.example.json').name}）")
    if spec.startswith("http"): return spec, spec.rsplit("/", 1)[-1]
    if spec in urls: return urls[spec], spec
    p = pathlib.Path(spec).expanduser()
    if p.exists():
        import fal_client
        name = p.stem.replace("zimage_", "")
        print(f"LoRA {p.name} を fal storage へアップロード（無料）→ {URLS_FILE.name} にキー '{name}' で登録")
        urls[name] = fal_client.upload_file(str(p)); jsave(URLS_FILE, urls); return urls[name], name
    sys.exit(f"LoRA が見つかりません: {spec}（登録済みキー: {', '.join(k for k in urls if not k.startswith('_')) or 'なし'}）\n"
             f"自分で学習した .safetensors を --lora <path> で一度渡すと fal storage へ上がり、{URLS_FILE.name} にキー登録されます")

def push_in(src, dst, crop=0.92):
    """終端フレーム＝中央8%プッシュイン（ローカル処理・無料）"""
    from PIL import Image
    im = Image.open(src).convert("RGB"); w, h = im.size
    cw, ch = round(w * crop), round(h * crop); x0, y0 = (w - cw) // 2, (h - ch) // 2
    im.crop((x0, y0, x0 + cw, y0 + ch)).resize((w, h), Image.LANCZOS).save(dst)

def guard(usd, A, what):
    if usd > A.max_usd: sys.exit(f"見積 ${usd:.2f} が上限 --max-usd {A.max_usd} を超えます（{what}）。本数を減らすか上限を上げてください")


# ---- frames ----
def cmd_frames(A):
    fr = pathlib.Path(A.out) / "frames"; fr.mkdir(parents=True, exist_ok=True)
    shots = load_shots(A.shots); seeds = [int(s) for s in A.seeds.split(",")]
    prompts = jload(fr / "prompts.json", {})
    groups = [("", A.strength)] + ([("_G0", 0.0)] if A.g0 else [])     # G0=同一文・LoRA 0 の対照群
    jobs = []
    if A.src_frames:   # 副経路: 既存フレーム → img2img 0.45
        srcs = sorted(p for p in pathlib.Path(A.src_frames).glob("*.png"))
        if not srcs: sys.exit(f"--src-frames に png がありません: {A.src_frames}")
        by_id = {sid: (body, cam) for sid, body, cam in shots}
        for p in srcs:
            sid = shot_id_of(p.stem); cap = p.with_suffix(".txt")
            body = by_id.get(sid, (None,))[0] or (cap.read_text().strip() if cap.exists() else None)
            if not body: sys.exit(f"{p.name}: プロンプトが引けません（shots.json の id か、同名 .txt キャプションを用意）")
            base = p.stem if re.search(r"_(start|end)$", p.stem) else p.stem + "_start"   # 単独画像は首として扱い終端は自動プッシュイン
            for tag, sc in groups:
                stem = base if not tag else re.sub(r"(_start|_end)$", tag + r"\1", base, count=1)
                mp = _mp(p); jobs.append(dict(kind="i2i", src=p, out=fr / f"{stem}.png", prompt=body + A.suffix, scale=sc, seed=seeds[0], mp=mp, body=body, cam=by_id.get(sid, (None, CAMERA))[1]))
    else:
        if not shots: sys.exit("--shots shots.json が必要です")
        for sid, body, cam in shots:
            for seed in seeds:
                for tag, sc in groups:
                    jobs.append(dict(kind="t2i", out=fr / f"{sid}_s{seed}{tag}_start.png", prompt=body + A.suffix, scale=sc, seed=seed, mp=T2I_W * T2I_H / 1e6, body=body, cam=cam))
    for j in jobs: prompts[re.sub(r"_(start|end)$", "", j["out"].stem)] = {"prompt": j["body"], "camera": j["cam"]}
    jsave(fr / "prompts.json", prompts)                       # i2v が同じ文を使う（群間で同一文にする）
    # i2v の --end-fallback 既定を決めるための経路メモ（副経路なら元動画の尾フレームの在り処も残す）
    jsave(fr / "route.json", {"route": "i2i", "src_frames": str(pathlib.Path(A.src_frames).resolve()), "denoise": A.denoise} if A.src_frames else {"route": "t2i"})
    todo = [j for j in jobs if not j["out"].exists()]
    est = sum(j["mp"] for j in todo) * USD_PER_MP
    print(f"[frames] {'img2img denoise %.2f' % A.denoise if A.src_frames else 't2i %dx%d' % (T2I_W, T2I_H)}  LoRA={A.lora} 強度={A.strength}  jobs={len(todo)}（既存スキップ {len(jobs) - len(todo)}）  見積 ${est:.3f}")
    for j in todo[:5]: print("   ", j["out"].name, "|", j["prompt"][:90])
    if len(todo) > 5: print(f"    … 他 {len(todo) - 5} 件")
    if not A.go: print("dry-run（課金なし）。実行は --go"); return
    if not todo: print("生成対象なし"); _ends(fr); return
    guard(est, A, "frames")
    load_env("FAL_KEY"); import fal_client
    lora_url, lora_name = resolve_lora(A.lora)
    from concurrent.futures import ThreadPoolExecutor
    def work(j):
        args = {"prompt": j["prompt"], "num_inference_steps": STEPS, "seed": j["seed"], "acceleration": "none", "enable_prompt_expansion": False,
                "num_images": 1, "output_format": "png", "loras": [{"path": lora_url, "scale": j["scale"]}]}
        if j["kind"] == "t2i": ep = T2I_ENDPOINT; args["image_size"] = {"width": T2I_W, "height": T2I_H}
        else: ep = I2I_ENDPOINT; args.update(image_url=fal_upload(j["src"], fr / "upload_urls.json"), strength=A.denoise, image_size="auto")
        for attempt in range(3):
            try:
                t = time.time(); res = fal_client.subscribe(ep, arguments=args); im = res["images"][0]
                import urllib.request; urllib.request.urlretrieve(im["url"], j["out"])
                rec = {"out": j["out"].name, "kind": j["kind"], "lora": lora_name, "strength": j["scale"], "seed": j["seed"], "prompt": j["prompt"], "w": im["width"], "h": im["height"],
                       "mp": round(im["width"] * im["height"] / 1e6, 3), "usd": round(im["width"] * im["height"] / 1e6 * USD_PER_MP, 4), "sec": round(time.time() - t, 1)}
                if j["kind"] == "i2i": rec["denoise"] = A.denoise
                break
            except Exception as e: rec = {"out": j["out"].name, "err": str(e)[:200], "attempt": attempt}; time.sleep(3)
        log_line(fr / "gen_log.jsonl", rec); print("   ", rec.get("out"), rec.get("err", f"${rec.get('usd')}"), flush=True); return rec
    with ThreadPoolExecutor(A.workers) as ex: res = list(ex.map(work, todo))
    ok = [r for r in res if "err" not in r]
    log_line(pathlib.Path(A.out) / "cost.jsonl", {"step": "frames", "n": len(ok), "usd": round(sum(r["usd"] for r in ok), 4), "lora": lora_name, "strength": A.strength, "t": time.strftime("%Y-%m-%d %H:%M")})
    print(f"[frames] 完了 {len(ok)}/{len(todo)}  実費 ${sum(r['usd'] for r in ok):.3f}")
    _ends(fr)

def _mp(p):
    from PIL import Image
    w, h = Image.open(p).size; return w * h / 1e6

def _ends(fr):
    n = 0
    for st in sorted(fr.glob("*_start.png")):
        en = fr / st.name.replace("_start.png", "_end.png")
        if not en.exists(): push_in(st, en); n += 1
    if n: print(f"[frames] 終端フレーム（8%プッシュイン）{n} 枚")


# ---- i2v ----
def end_rejected(c):
    """動画API側が「終端フレーム（content[2]）」を入力画像として弾いたか＝終端差し替えで通せる拒否か。
    実在人物検出（InputImageSensitiveContentDetected.PrivacyInformation 等）を想定。首（content[1]）拒否は対象外。"""
    b = (c.get("create_body") or "").lower()
    return c.get("create_status") == 400 and "content[2]" in b and ("sensitivecontent" in b or "privacyinformation" in b or "real person" in b)


def resolve_end_fallback(A, route):
    """--end-fallback 未指定時の既定: 副経路（img2img）= original（元動画の尾をそのまま）／主経路（t2i）= push-in。
    元動画の尾が無い副経路は push-in に落とす。"""
    if A.end_fallback: return A.end_fallback
    return "original" if route.get("route") == "i2i" and route.get("src_frames") else "push-in"


def fallback_chain(mode):
    """差し替えの順番。original は**元動画の尾そのものも実在人物検出で弾かれることがある**ため、
    original → push-in の2段で必ず通す。push-in 単独・none も選べる。"""
    return {"original": ["original", "push-in"], "push-in": ["push-in"], "none": []}.get(mode, ["push-in"])


def fallback_end(step, stem, start_img, fr, route):
    """差し替え画像を1段ぶん用意する。戻り値 (path, 使った手段)／用意できなければ (None, None)"""
    if step == "original":
        src = pathlib.Path(route.get("src_frames") or "") / f"{stem}_end.png"
        return (src, "original") if src.exists() else (None, None)
    fb = fr / f"{stem}_end_fb.png"; push_in(start_img, fb); return fb, "push-in"


R2V_TOK_PER_CLIP = 130_700          # R2V は i2v の約1.2倍（5秒の概算）
R2V_KEEP = ("@Video1 の動き・カメラワーク・歩行のタイミングをそのまま保ち、"
            "@Image1 の人物・衣装・ルックに置き換える。カット割り・構図・尺は @Video1 のまま変えない。")


def ref_video_for(A, stem):
    """--ref-video: 単一ファイル／URL／ショット別ディレクトリ（<stem>.mp4 か <shot_id>.mp4）を解決"""
    v = A.ref_video
    if not v: return None
    if str(v).startswith("http"): return v
    p = pathlib.Path(v).expanduser()
    if p.is_dir():
        for name in (f"{stem}.mp4", f"{shot_id_of(stem)}.mp4"):
            if (p / name).exists(): return p / name
        return None
    return p if p.exists() else None


def video_guard(A):
    """動画化（i2v/post/all）は既定で停止。LoRA を通した画像は一部の動画APIで来歴認証が切れ、人物が写って
    いると拒否されることがあるため、本ツールは画像までを範囲とする。意図して使う場合のみ --force-video。"""
    if getattr(A, "force_video", False): return
    print("[look-lora] 動画生成（i2v/post/all）は既定で停止しています。"
          "LoRA を通した画像は一部の動画APIで来歴認証が切れ、人物が写っていると拒否されることがあります。"
          "承知のうえで使う場合のみ --force-video を付けて実行してください。")
    sys.exit(1)


def cmd_i2v(A):
    video_guard(A)
    out = pathlib.Path(A.out); fr = pathlib.Path(A.frames or out / "frames"); raw = out / "raw"; rec_dir = out / "rec"
    prompts = jload(fr / "prompts.json", {}); shots = {sid: (body, cam) for sid, body, cam in load_shots(A.shots)}
    pairs = []
    for st in sorted(fr.glob("*_start.png")):
        stem = st.name[:-len("_start.png")]; en = fr / f"{stem}_end.png"
        rv = ref_video_for(A, stem)
        if A.ref_video and rv is None: print(f"    skip {stem}: --ref-video に対応する動画が無い"); continue
        if not rv and not en.exists(): print(f"    skip {stem}: _end.png なし"); continue
        if (raw / f"{stem}.mp4").exists(): continue
        p = prompts.get(stem) or (shots.get(shot_id_of(stem)) and dict(zip(("prompt", "camera"), shots[shot_id_of(stem)])))
        if not p: sys.exit(f"{stem}: プロンプトが引けません（frames/prompts.json か --shots）")
        if rv:   # R2V: ショット固有文＋「元動画の動きを保つ」定型（汎用カメラ文は使わない）
            if not p.get("prompt"): sys.exit(f"{stem}: R2V はショット固有の prompt が必須です（shots.json）")
            pairs.append((stem, st, None, f"{R2V_KEEP}{p['prompt']}", rv))
        else:
            pairs.append((stem, st, en, f"{p['prompt']}, {A.camera or p.get('camera') or CAMERA}", None))
    n_plan = len(pairs)
    if A.cmd == "all" and not A.go and not pairs:   # all の dry-run: frames 未生成でも本数から見積る
        n_plan = len(load_shots(A.shots)) * len(A.seeds.split(",")) * (2 if A.g0 else 1)
    tok = R2V_TOK_PER_CLIP if A.ref_video else TOK_PER_CLIP
    est = n_plan * tok / 1e6 * USD_PER_MTOK
    kind = "R2V（@Video1 参照動画＋@Image1 首アンカー）" if A.ref_video else "first+last"
    print(f"[i2v] Seedance 2.5 {kind} 720p/5s vseed={A.vseed}  jobs={n_plan}  見積 {n_plan * tok:,} tok ≒ ${est:.2f}{'（R2V は概算）' if A.ref_video else ''}")
    for stem, _, _, pr, rv in pairs[:5]: print("   ", stem, "|", (f"ref={pathlib.Path(rv).name} | " if rv and not str(rv).startswith("http") else ""), pr[:80])
    if len(pairs) > 5: print(f"    … 他 {len(pairs) - 5} 件")
    if not A.go: print("dry-run（課金なし）。実行は --go"); return
    if not pairs: print("生成対象なし"); return
    guard(est, A, "i2v")
    load_env("FAL_KEY", "ARK_API_KEY"); import requests
    raw.mkdir(parents=True, exist_ok=True); rec_dir.mkdir(exist_ok=True)
    H = {"Authorization": "Bearer " + os.environ["ARK_API_KEY"], "Content-Type": "application/json"}
    route = jload(fr / "route.json", {}); mode = resolve_end_fallback(A, route)
    print(f"[i2v] end-fallback={mode}（経路 {route.get('route', 't2i')}{'・元尾 ' + route['src_frames'] if mode == 'original' else ''}）")
    def save(r): jsave(rec_dir / f"rec_{r['stem']}.json", r)
    def create(pr, first_url, last_url, video_url=None):
        """create を1回投げる（429/5xx のみリトライ）。戻り値 = {create_status, create_body, task_id}
        video_url ありは R2V: @Video1=reference_video（role 必須・無いと 400）＋ @Image1=reference_image（LoRA塗り首）。"""
        if video_url:
            content = [{"type": "text", "text": pr},
                       {"type": "video_url", "video_url": {"url": video_url}, "role": "reference_video"},
                       {"type": "image_url", "image_url": {"url": first_url}, "role": "reference_image"}]
        else:
            content = [{"type": "text", "text": pr},
                       {"type": "image_url", "image_url": {"url": first_url}, "role": "first_frame"},
                       {"type": "image_url", "image_url": {"url": last_url}, "role": "last_frame"}]
        body = {"model": I2V_MODEL, "content": content,
                "resolution": "720p", "duration": 5, "generate_audio": False, "seed": A.vseed, "watermark": False}
        c = {"create_status": None, "create_body": None, "task_id": None}
        for attempt in range(6):
            resp = requests.post(f"{ARK_BASE}/contents/generations/tasks", headers=H, json=body, timeout=60)
            c["create_status"] = resp.status_code; c["create_body"] = resp.text[:400]
            if resp.status_code == 200: c["task_id"] = resp.json()["id"]; break
            if resp.status_code == 429 or resp.status_code >= 500: time.sleep(30 * (attempt + 1)); continue
            break
        return c
    recs = []
    for stem, st, en, pr, rv in pairs:
        first_url = fal_upload(st, out / "upload_urls.json")
        r = {"stem": stem, "prompt": pr, "vseed": A.vseed, "t0": time.time(), "verdict": "REJECTED_AT_CREATE"}
        if rv:   # R2V: 参照動画＋首アンカーのみ（終端なし＝end-fallback は無関係）
            vurl = rv if str(rv).startswith("http") else fal_upload(rv, out / "upload_urls.json")
            r["mode"] = "r2v"; r["ref_video"] = str(rv)
            c = create(pr, first_url, None, video_url=vurl)
        else:
            c = create(pr, first_url, fal_upload(en, out / "upload_urls.json"))
            # 実在人物検出などで終端（content[2]）が弾かれたら、終端を差し替えて再投入。
            # original＝元動画の尾フレームそのまま（動きが保たれる・副経路の既定）／push-in＝首の中央8%プッシュイン
            for step in fallback_chain(mode):
                if c["task_id"] or not end_rejected(c): break
                fb, used = fallback_end(step, stem, st, fr, route)
                if fb is None: continue
                r.setdefault("rejected_end_bodies", []).append(c["create_body"])
                r["end_fallback"] = used; r["end_fallback_file"] = str(fb)
                print(f"    終端拒否 {stem} → {used}（{fb.name}）で再投入", flush=True)
                c = create(pr, first_url, fal_upload(fb, out / "upload_urls.json"))
        r["create_status"] = c["create_status"]; r["create_body"] = c["create_body"]
        if c["task_id"]: r["task_id"] = c["task_id"]; r["verdict"] = "RUNNING"
        save(r); recs.append(r); print("    create", stem, r["verdict"], r.get("task_id"), flush=True)
    while any(r["verdict"] == "RUNNING" for r in recs):
        time.sleep(20)
        for r in recs:
            if r["verdict"] != "RUNNING": continue
            try: d = requests.get(f"{ARK_BASE}/contents/generations/tasks/{r['task_id']}", headers=H, timeout=60).json()
            except Exception as e: print("    poll err", r["stem"], repr(e)[:100], flush=True); continue
            st_ = d.get("status")
            if st_ == "succeeded":
                (raw / f"{r['stem']}.mp4").write_bytes(requests.get(d["content"]["video_url"], timeout=300).content)
                r["usage"] = d.get("usage"); r["out"] = f"raw/{r['stem']}.mp4"; r["verdict"] = "ACCEPTED"
            elif st_ in ("failed", "cancelled", "expired"): r["verdict"] = "FAILED_" + st_; r["fail_body"] = json.dumps(d)[:600]
            elif time.time() - r["t0"] > 1800: r["verdict"] = "TIMEOUT"
            if r["verdict"] != "RUNNING":
                r["elapsed_s"] = round(time.time() - r["t0"]); save(r)
                print("    done", r["stem"], r["verdict"], (r.get("usage") or {}).get("total_tokens"), r["elapsed_s"], "s", flush=True)
    tok = sum((r.get("usage") or {}).get("total_tokens", 0) for r in recs); ok = sum(r["verdict"] == "ACCEPTED" for r in recs)
    log_line(out / "cost.jsonl", {"step": "i2v", "n": ok, "tok": tok, "usd": round(tok / 1e6 * USD_PER_MTOK, 2), "t": time.strftime("%Y-%m-%d %H:%M")})
    print(f"[i2v] 完了 {ok}/{len(recs)}  usage {tok:,} tok ≒ ${tok / 1e6 * USD_PER_MTOK:.2f}")


# ---- post ----
def cmd_post(A):
    video_guard(A)
    out = pathlib.Path(A.out); src = pathlib.Path(A.raw or out / "raw"); dst = pathlib.Path(A.prores or out / "prores"); dst.mkdir(parents=True, exist_ok=True)
    files = [src] if src.is_file() else sorted(src.glob("*.mp4"))
    if not files: print(f"[post] mp4 がありません: {src}"); return
    n = 0
    for f in files:
        mov = dst / (f.stem + ".mov")
        if mov.exists(): print(f"    {f.stem}: exists"); continue
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(f), "-vf", f"noise=alls={A.grain}:allf=t+u,format=yuv444p10le",
               "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuv444p10le", "-an", str(mov)]
        if subprocess.run(cmd).returncode != 0: sys.exit(f"ffmpeg 失敗: {f.name}")
        info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,pix_fmt,width,height,nb_frames,r_frame_rate", "-of", "csv=p=0", str(mov)], capture_output=True, text=True).stdout.strip()
        print(f"    {f.stem}: {info} {mov.stat().st_size >> 20}MB"); n += 1
    print(f"[post] 変換 {n} 本（技術グレイン alls={A.grain}・グレード前・ProRes 4444）→ {dst}\n      次: カラーグレード（色は LoRA の外で決める）")


# ---- main ----
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    def common(p, paid=True):
        p.add_argument("--out", default="lookshot_out", help="出力ルート（frames/ raw/ prores/ rec/ cost.jsonl）")
        p.add_argument("--shots", default=None, help="shots.json（配列 or 辞書・ルック語なしの内容文）")
        if paid:
            p.add_argument("--go", action="store_true", help="課金実行（無指定=dry-run 見積のみ）")
            p.add_argument("--max-usd", type=float, default=10.0, help="この見積を超えたら実行しない")
    f = sp.add_parser("frames", help="首尾フレーム生成（t2i＋LoRA／--src-frames で img2img 副経路）"); common(f)
    f.add_argument("--lora", default=DEFAULT_LORA, help="lora_urls.json のキー／URL／ローカル .safetensors")
    f.add_argument("--strength", type=float, default=DEFAULT_STRENGTH); f.add_argument("--seeds", "--seed", default="1234")
    f.add_argument("--suffix", default=LOOK_SUFFIX, help="プロンプト末尾の look語"); f.add_argument("--g0", action="store_true", help="同一文・LoRA 0 の対照フレームも作る")
    f.add_argument("--src-frames", default=None, help="副経路: 既存フレーム dir（png＋任意の同名 .txt）"); f.add_argument("--denoise", type=float, default=DENOISE)
    f.add_argument("--workers", type=int, default=3)
    v = sp.add_parser("i2v", help="[既定で停止] 首尾 → Seedance 2.5 first+last 5s"); common(v)
    v.add_argument("--force-video", action="store_true", help="既定の停止ガードを解除する")
    v.add_argument("--frames", default=None, help="首尾フレーム dir（既定 <out>/frames）"); v.add_argument("--vseed", type=int, default=I2V_VSEED); v.add_argument("--camera", default=None, help="カメラ文の上書き（既定 CAMERA）")
    v.add_argument("--ref-video", default=None, help="R2V（参照動画）経路: 元動画のファイル／URL／ショット別ディレクトリ。**既存動画のリスタイルではこれを使う**（首尾2枚＋汎用カメラ文では動きが死ぬ）。LoRA塗りの開始フレームを @Image1、元動画を @Video1 として渡し、尾フレームは使わない")
    v.add_argument("--end-fallback", choices=["original", "push-in", "none"], default=None, help="終端が動画API側に拒否されたときの差し替え（1回だけ再投入）。original=元動画の尾フレームそのまま／push-in=首の中央8%%プッシュイン／none=無効。既定は副経路(img2img)=original・主経路(t2i)=push-in")
    p = sp.add_parser("post", help="[既定で停止] raw mp4 → グレイン → ProRes 4444（無料）"); common(p, paid=False)
    p.add_argument("--force-video", action="store_true", help="既定の停止ガードを解除する")
    p.add_argument("--raw", default=None, help="mp4 の dir か単一ファイル（既定 <out>/raw）"); p.add_argument("--prores", default=None); p.add_argument("--grain", type=int, default=GRAIN)
    a = sp.add_parser("all", help="[既定で停止] frames → i2v → post"); common(a)
    a.add_argument("--force-video", action="store_true", help="既定の停止ガードを解除する")
    for o, kw in [("--lora", dict(default=DEFAULT_LORA)), ("--strength", dict(type=float, default=DEFAULT_STRENGTH)), ("--seeds", dict(default="1234")), ("--suffix", dict(default=LOOK_SUFFIX)),
                  ("--g0", dict(action="store_true")), ("--src-frames", dict(default=None)), ("--denoise", dict(type=float, default=DENOISE)), ("--workers", dict(type=int, default=3)),
                  ("--vseed", dict(type=int, default=I2V_VSEED)), ("--camera", dict(default=None)), ("--grain", dict(type=int, default=GRAIN)),
                  ("--end-fallback", dict(choices=["original", "push-in", "none"], default=None)),
                  ("--ref-video", dict(default=None))]: a.add_argument(o, **kw)
    A = ap.parse_args()
    if not hasattr(A, "ref_video"): A.ref_video = None
    if A.cmd == "frames": cmd_frames(A)
    elif A.cmd == "i2v": cmd_i2v(A)
    elif A.cmd == "post": cmd_post(A)
    else:
        video_guard(A)   # all は frames にも課金が乗る前にガードで止める
        A.frames = A.raw = A.prores = None
        cmd_frames(A); cmd_i2v(A)
        if A.go: cmd_post(A)

if __name__ == "__main__":
    main()
