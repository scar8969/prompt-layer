"""End-to-end sanity over the real HTTP surface of dev.py (the Vercel layout).

Run:  python scripts/sanity.py     (dev.py must be listening on :8090)
"""

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
fails = []
checks = 0


def call(method, path, payload=None, raw=False):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            return resp.status, (body if raw else json.loads(body))
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode()
            return exc.code, (text if raw else json.loads(text))
        except Exception:
            return exc.code, {}


def ok(label, cond, extra=""):
    global checks
    checks += 1
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else "  <- " + str(extra)[:220]))
    if not cond:
        fails.append(label)


print("== the read endpoints answer, and only those ==")
for path in ("/api/health", "/api/models", "/api/presets", "/api/storyboard",
             "/api/reference", "/api/video"):
    status, body = call("GET", path)
    ok(f"GET {path} -> 200 success", status == 200 and body.get("success") is True, (status, body))
# preflight, improve and compile are POST-only: a GET must not be a way in.
for path in ("/api/preflight", "/api/improve", "/api/compile"):
    status, _ = call("GET", path)
    ok(f"GET {path} -> 501 (POST-only, no do_GET)", status == 501, status)

print("== index.html is served, and only it ==")
status, html = call("GET", "/", raw=True)
ok("GET / -> 200 html", status == 200 and "<html" in html.lower(), status)
ok("GET /dev.py -> 404 (allowlist, not a file server)", call("GET", "/dev.py")[0] == 404)

print("== models payload carries the new indexes ==")
_, models = call("GET", "/api/models")
sb = models.get("storyboard") or {}
gen = models.get("generation") or {}
dsn = models.get("design") or {}
ok("models.storyboard has facets+roles+rules", bool(sb.get("facets") and sb.get("roles") and sb.get("rules")), list(sb))
ok("models.generation reports the gate closed", gen.get("enabled") is False and "dry run only" in gen.get("reason", ""), gen)
ok("models.design still has the vocab", bool((dsn.get("vocab") or {}).get("camera_move")), list(dsn))
ok("every storyboard rule has source+evidence",
   all(r.get("source") and r.get("evidence") for r in (sb.get("rules") or {}).values()), sb.get("rules"))

print("== the chat turn ==")
_, plan1 = call("POST", "/api/storyboard", {"action": "plan", "concept": "a video", "model": "sora-2"})
d = plan1.get("data") or {}
ok("bare concept: not ready, three blocking",
   d.get("ready") is False and set(d.get("blocking_missing") or []) == {"subject", "action", "setting"},
   d.get("blocking_missing"))
ok("every question carries why/source/evidence",
   bool(d.get("questions")) and all(q.get("why") and q.get("source") and q.get("evidence") for q in d["questions"]),
   d.get("questions"))
ok("no question repeats its own options",
   all(o not in q["question"] for q in d["questions"] for o in (q.get("options") or [])))

_, plan2 = call("POST", "/api/storyboard", {"action": "plan", "model": "sora-2",
    "concept": "A detective named Mara walks through a rain-soaked alley at night. Then she finds a glowing book.",
    "answers": {"camera_move": "slow pan left"}})
d2 = plan2.get("data") or {}
ok("an answered gap is no longer asked",
   not any(q["facet"] == "camera_move" for q in d2.get("questions") or []),
   [q["facet"] for q in d2.get("questions") or []])
ok("a multi-beat concept raises the sourced warning",
   any(w["id"] == "multiple_beats" and "Generate each part as a separate clip" in w["evidence"]
       for w in d2.get("warnings") or []), d2.get("warnings"))

print("== the scenes ==")
_, sc = call("POST", "/api/storyboard", {"action": "scenes", "model": "sora-2",
    "concept": "A detective named Mara walks through a rain-soaked alley at night. Then she finds a glowing book in a doorway and picks it up.",
    "answers": {"camera_move": "slow pan left", "duration": "8 seconds each"},
    "palette": "amber, cream, walnut brown", "template": "labelled"})
d3 = sc.get("data") or {}
scenes = d3.get("scenes") or []
ok("one scene per beat", [s["id"] for s in scenes] == ["scene-1", "scene-2"], [s["id"] for s in scenes])
ok("the chat's duration became the parameter",
   d3.get("totals", {}).get("per_clip") == "8" and all(s["params"].get("seconds") == "8" for s in scenes),
   d3.get("totals"))
ok("the top-level palette was merged, not dropped",
   "Palette anchors: amber, cream, walnut brown" in scenes[0]["prompt"], scenes[0]["prompt"][:120])
ok("the chat's camera move reached the prompt", "slow pan left" in scenes[0]["prompt"], scenes[0]["prompt"][:160])
ok("every merged element is sourced",
   all(e.get("source") and e.get("evidence") for s in scenes for e in s.get("added") or []),
   [e for s in scenes for e in s.get("added") or []])
ok("the scene is not double-merged", scenes[0]["prompt"].count("slow pan left") == 1, scenes[0]["prompt"])
ok("answers_placed / answers_unplaced are reported",
   d3.get("answers_placed") is not None and d3.get("answers_unplaced") == [],
   (d3.get("answers_placed"), d3.get("answers_unplaced")))

_, un = call("POST", "/api/storyboard", {"action": "scenes", "model": "sora-2",
    "concept": "a fox crosses a field", "answers": {"subject": "a second fox"}})
d4 = un.get("data") or {}
ok("an unplaceable answer is reported, not dropped",
   d4.get("answers_unplaced") == ["subject"] and any(n["id"] == "answer_not_placed" for n in d4.get("notes") or []),
   d4.get("answers_unplaced"))

print("== reference images ==")
_, ref1 = call("POST", "/api/reference", {"model": "sora-2", "images": [
    {"name": "f.png", "role": "first_frame", "width": 1920, "height": 1080,
     "type": "image/png", "bytes": 100, "palette": ["#111133", "#ccbb88"]}]})
rd = ref1.get("data") or {}
ok("a size the endpoint does not document is flagged",
   any(c["id"] == "reference_size" for c in rd.get("checks") or []), rd.get("checks"))
ok("the flagged size cites the guide",
   any("720x1280" in (c.get("detail") or "") and c.get("source") for c in rd.get("checks") or []), rd.get("checks"))
ok("only measurements come back",
   set((rd.get("images") or [{}])[0]) == {"index", "role", "role_asked", "name", "width", "height",
                                         "type", "bytes", "palette", "scene"},
   list((rd.get("images") or [{}])[0]))
ok("the palette line is the documented shape",
   (rd.get("palette_line") or {}).get("line") == "Palette anchors: #111133, #ccbb88", rd.get("palette_line"))

_, ref2 = call("POST", "/api/reference", {"model": "veo3-fast", "images": [
    {"name": "f.png", "role": "first_frame", "width": 1280, "height": 720,
     "type": "image/png", "bytes": 100, "palette": []}]})
ok("an image on a text-to-video endpoint is reported unusable, not dropped",
   any(c["id"] == "role_not_supported" for c in (ref2.get("data") or {}).get("checks") or []),
   (ref2.get("data") or {}).get("checks"))

_, ref3 = call("POST", "/api/reference", {"model": "hailuo-02-i2v", "images": [
    {"name": "f.png", "role": "first_frame", "width": 1920, "height": 1080,
     "type": "image/png", "bytes": 100, "palette": []}]})
ok("a label-only endpoint (768P) makes no pixel-size claim",
   not any(c["id"] == "reference_size" for c in (ref3.get("data") or {}).get("checks") or []),
   (ref3.get("data") or {}).get("checks"))

print("== the generation gate ==")
_, vid = call("POST", "/api/video", {"action": "plan", "model": "sora-2",
                                     "concept": "A lantern drifts down a river at dusk"})
vd = vid.get("data") or {}
ok("the plan is a dry run", vd.get("dry_run") is True, vd.get("dry_run"))
ok("the gate names the three missing variables",
   set((vd.get("gate") or {}).get("missing") or []) ==
   {"ALLOW_REMOTE_GENERATION=1", "VIDEO_GATEWAY_URL", "VIDEO_API_KEY"},
   (vd.get("gate") or {}).get("missing"))
ok("one job with a real body",
   len(vd.get("jobs") or []) == 1 and (vd["jobs"][0].get("body") or {}).get("prompt"), vd.get("jobs"))

_, sub = call("POST", "/api/video", {"action": "submit", "model": "sora-2",
                                     "concept": "A lantern drifts down a river at dusk", "confirm": True})
ok("submit refuses without the gate even with confirm=true",
   (sub.get("data") or {}).get("submitted") is False and
   "dry run only" in ((sub.get("data") or {}).get("reason") or ""), sub.get("data"))

_, vid2 = call("POST", "/api/video", {"action": "plan", "model": "hailuo-02-i2v",
    "concept": "A lantern drifts down a river at dusk",
    "references": [{"name": "f.png", "role": "first_frame", "width": 1280, "height": 720,
                    "type": "image/png", "bytes": 100, "palette": []}]})
job = ((vid2.get("data") or {}).get("jobs") or [{}])[0]
ok("an image field is a placeholder, never pixels",
   str(job.get("body", {}).get("first_frame_image", "")).startswith("<attach ") and job.get("image_fields"),
   job.get("body"))

print("== failure paths refuse ==")
bad = [
    ("/api/storyboard", {"action": "plan"}),
    ("/api/storyboard", {"action": "dance", "concept": "x"}),
    ("/api/storyboard", {"action": "plan", "concept": "x", "model": "not-a-model"}),
    ("/api/storyboard", {"action": "scenes", "concept": "x", "design": "bold"}),
    ("/api/reference", {"model": "sora-2", "images": "f.png"}),
    ("/api/video", {"action": "burn", "concept": "x"}),
    ("/api/video", {"action": "plan"}),
    ("/api/video", {"action": "plan", "concept": "x", "model": "not-a-model"}),
]
for path, payload in bad:
    status, _ = call("POST", path, payload)
    ok(f"POST {path} {json.dumps(payload)[:50]} -> 400", status == 400, status)

print("== an unknown model is answered, not refused ==")
# The honesty rule: a model we have not probed gets generic layers and
# unknown_model: true. It is not an error, and it is not a made-up default.
_, unk = call("POST", "/api/preflight", {"model": "nope", "prompt": "x"})
ud = unk.get("data") or {}
ok("preflight answers an unknown model with unknown_model: true",
   unk.get("success") is True and ud.get("unknown_model") is True, (unk.get("success"), ud.get("unknown_model")))
ok("and still gives the generic layers",
   bool(ud.get("generic_layers") or ud.get("conditional_layers")), list(ud))
_, unk2 = call("POST", "/api/improve", {"model": "nope", "prompt": "x"})
ok("improve answers an unknown model rather than erroring", unk2.get("success") is True, unk2.get("success"))

print("== the pre-existing tools still work ==")
# With the layer left on, the rewrite is reported; the patch is what turns it off.
_, pre = call("POST", "/api/preflight", {"model": "hailuo-02-standard",
                                         "prompt": "a cat surfing a huge wave"})
pd = pre.get("data") or {}
ok("preflight reports the upstream rewrite when the layer is on",
   pd.get("prompt", {}).get("rewritten_upstream") is True, pd.get("prompt", {}).get("rewritten_upstream"))
ok("and hands back the patch that switches every additive layer off",
   (pd.get("full_control_patch") or {}).get("prompt_optimizer") is False,
   pd.get("full_control_patch"))
_, pre2 = call("POST", "/api/preflight", {"model": "hailuo-02-standard",
    "prompt": "a cat surfing a huge wave", "options": {"prompt_optimizer": False}})
ok("and honours the override when the caller sets it",
   (pre2.get("data") or {}).get("prompt", {}).get("rewritten_upstream") is False,
   (pre2.get("data") or {}).get("prompt", {}).get("rewritten_upstream"))
_, imp = call("POST", "/api/improve", {"model": "hailuo-02-standard", "clean": True,
    "prompt": "A beautiful cinematic 8k scene of a man. Camera dollies in and pans left. Make it longer.",
    "selections": {"camera_move": "dolly in", "shot_size": "close-up shot"}})
idd = imp.get("data") or {}
ok("improve raises the score and logs the removals",
   idd.get("after", {}).get("score", 0) > idd.get("before", {}).get("score", 0) and
   idd.get("cleaned", {}).get("changes"),
   (idd.get("before", {}).get("score"), idd.get("after", {}).get("score")))
_, cmp_ = call("POST", "/api/compile", {"prompt": "a cat surfing", "style": "wan-engineer", "dry_run": True})
ok("compile answers and stays a dry run",
   cmp_.get("success") is True and (cmp_.get("data") or {}).get("dry_run") is True, cmp_)
_, pst = call("GET", "/api/presets?lang=en&task=fill-mask")
ok("the Hugging Face presets are still parsed", bool((pst.get("data") or {}).get("presets")), pst)

print("== image endpoints == ")
_, mlist = call("GET", "/api/models")
kinds = {}
for m in (mlist.get("data") or []):
    kinds.setdefault(m["kind"], []).append(m["slug"])
ok("the dataset carries the image endpoints next to the video ones",
   sorted(kinds.get("image") or []) == ["flux-2", "flux-2-edit", "flux-2-pro", "nano-banana",
                                        "qwen-image", "seedream-v4"], kinds.get("image"))
ok("and the video and chat endpoints are still there",
   len(kinds.get("video") or []) == 13 and kinds.get("chat") == ["gpt-4o-mini"],
   (len(kinds.get("video") or []), kinds.get("chat")))
_, ip = call("POST", "/api/preflight", {"model": "flux-2", "prompt": "a red sneaker on wet asphalt"})
ipd = ip.get("data") or {}
ok("an image prompt is preflighted against the image endpoint",
   [l["id"] for l in ipd.get("layers") or []] ==
   ["expand.prompt_expansion", "screen.safety_checker", "pin.image_size"], ipd.get("layers"))
ok("the off-switches are the image endpoint's own parameters",
   ipd.get("full_control_patch") == {"enable_prompt_expansion": False,
                                     "enable_safety_checker": False}, ipd.get("full_control_patch"))
ok("a layer that is off by default is reported as off, not as active",
   not any(l["kind"] == "expand" for l in ipd.get("layers") or [] if l["active"]), ipd.get("layers"))
_, sd = call("POST", "/api/preflight", {"model": "seedream-v4", "prompt": "a paper crane"})
ok("an endpoint with no off-switch is not handed an invented one",
   (sd.get("data") or {}).get("full_control_patch") == {"enable_safety_checker": False},
   (sd.get("data") or {}).get("full_control_patch"))
ok("the chat-only serving layer is scoped out of an image request",
   "serving.system_message" not in [l["id"] for l in (sd.get("data") or {}).get("layers") or []],
   [l["id"] for l in (sd.get("data") or {}).get("layers") or []])
_, r2i = call("POST", "/api/reference", {"model": "flux-2", "role": "subject",
    "images": [{"name": "a.png", "role": "subject", "width": 1280, "height": 720,
                "type": "image/png", "bytes": 100}]})
ok("a text-to-image endpoint refuses an image it cannot take",
   [c["id"] for c in (r2i.get("data") or {}).get("checks") or []] == ["role_not_supported"],
   (r2i.get("data") or {}).get("checks"))
_, ed = call("POST", "/api/reference", {"model": "flux-2-edit", "role": "subject",
    "images": [{"name": "a.png", "role": "subject", "width": 1280, "height": 720,
                "type": "image/png", "bytes": 100}]})
ok("and the edit endpoint says where the image goes",
   "image_urls" in json.dumps((ed.get("data") or {}).get("sheet") or {}),
   (ed.get("data") or {}).get("sheet"))

print("== the endpoint you are discussing decides the answers ==")
CONCEPT = "a lighthouse keeper walks out onto the pier at dawn"
def duration_options(slug):
    _, r = call("POST", "/api/storyboard", {"action": "plan", "concept": CONCEPT, "model": slug})
    q = next((q for q in (r.get("data") or {}).get("questions") or [] if q["facet"] == "duration"), {})
    return q
sora_q, veo_q = duration_options("sora-2"), duration_options("veo3-fast")
ok("two discussion models offer their own documented clip lengths",
   sora_q.get("options") == ["4", "8", "12", "16", "20"] and veo_q.get("options") == ["8s"],
   (sora_q.get("options"), veo_q.get("options")))
ok("and each question names the parameter the values came from",
   sora_q.get("options_from", {}).get("parameter") == "seconds"
   and veo_q.get("options_from", {}).get("parameter") == "duration",
   (sora_q.get("options_from"), veo_q.get("options_from")))
ok("the rule behind the question is still the published one, with its quote",
   bool(veo_q.get("evidence")) and veo_q.get("evidence") == sora_q.get("evidence"),
   (veo_q.get("evidence"), sora_q.get("evidence")))
_, mix = call("POST", "/api/storyboard", {"action": "scenes", "concept": CONCEPT,
                                          "model": "hailuo-02-i2v"})
mix_scene = ((mix.get("data") or {}).get("scenes") or [{}])[0]
ok("the scenes are built for the model the scenes were asked for",
   (mix_scene.get("params") or {}).get("duration") == "6", mix_scene.get("params"))

print()
print(f"{checks - len(fails)}/{checks} checks passed")
if fails:
    print("FAILURES:")
    for name in fails:
        print("  -", name)
    sys.exit(1)
print("SANITY OK")
