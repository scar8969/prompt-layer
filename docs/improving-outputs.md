# How to actually improve the output

Ordered by how much each lever usually moves the result. Every claim here is one
the tool can show you, with the guide it came from — nothing is a vibe.

## 1. Stop asking in prose for things that are parameters

`4k`, `1080p`, `16:9`, `8 seconds`, `make it longer`, `higher quality` — the
endpoint has a field for each, and the field wins. In prose they either do nothing
or get treated as part of the scene description.

> ✗ `a man walking through a market, 4k, 16:9, make it longer`
> ✓ `a man walking through a market` + `resolution: 4k, aspect_ratio: 16:9, duration: 8`

Source: Sora 2 prompting guide — *"Don't include … resolution, aspect ratio, or
duration in the prompt text; use the parameters."*

## 2. Delete the feeling-words

`beautiful`, `stunning`, `cinematic`, `epic`, `masterpiece`, `8k` are not
descriptions — they are opinions. They cost tokens and bias the model toward
generic stock footage. Replace each one with the thing that made you feel it.

> ✗ `a beautiful cinematic shot of a warrior`
> ✓ `a warrior in wet leather armour, low sun behind her, dust in the air`

If you keep `cinematic`, give it a reason: `cinematic, anamorphic, 35mm, dusk
light` is a technical instruction; bare `cinematic` is a wish.

Source: Sora 2 guide, Veo prompt guide (the "cinematic" warning).

## 3. One shot, one move

A 4–8 second generation is a *shot*, not a scene. Two camera moves means the model
averages them into a wobble; opposing moves (`dolly in` while `pans left`) means it
picks one and ignores the rest — usually not the one you wanted.

> ✗ `the camera dollies in and pans left while orbiting`
> ✓ `slow dolly in`

More shots = more generations, cut together afterwards.

Source: Wan 2.2 system prompt (`wan/utils/system_prompt.py`) — one camera movement
per shot.

## 4. Write the light and the lens

These two change the look more than any adjective, and they are the ones people
skip. Name the source, the quality and the direction; name the lens and the depth
of field.

> `low sun behind her, hard rim light, deep shadow` · `85mm, shallow depth of
> field, background falling out of focus`

Source: Veo prompt guide (lighting, lens and film-stock language); Wan 2.2 system
prompt (shot size + camera + lighting as separate fields).

## 5. Describe the action in beats

`a man walks across the room` gives the model one instruction for 5 seconds, so it
fills the rest with drift. Give it two or three beats with a *change* in them.

> ✗ `a man walks across the room`
> ✓ `a man crosses the workshop and stops; the lamp above him flickers once, and
> he looks up`

Source: Sora 2 guide (action beats, "something happens"), Veo guide (motion and
timing).

## 6. Know what the endpoint adds, and decide whether you want it

This is the one nobody tells you, and it is measurable:

| what | where | default |
|---|---|---|
| your prompt **rewritten** if a policy check fails | Veo 3 / Veo 3 Fast (`auto_fix`) | `true` |
| your prompt **expanded by an LLM** | Hailuo-02 (`prompt_optimizer`), LTX-2 (`enable_prompt_expansion`) | `true` |
| a **canned negative prompt** attached | Kling (`"blur, distort, and low quality"`), Pika, LTX-2 (~200 words) | on |
| **screened** before it runs | `enable_safety_checker`, `safety_tolerance` | `true` / `"4"` |
| **resolution, aspect, duration, audio** pinned | every hosted endpoint | provider's choice |

If an endpoint expands your prompt, the thing being prompted is not your prompt —
it is the expander's rewrite of it. Either write for the expander (short, plain,
no camera jargon) or turn it off and write the whole thing yourself.

## Reference images change the rules

With an image, the composition and the look are already decided. The prompt's job
shrinks to **what moves and what changes**:

> ✗ `a woman in a red coat standing in a snowy street, wide shot, cold light`
> ✓ `she turns her head toward the camera; snow falls past her shoulder`

Also: the image's aspect ratio usually wins over any `aspect_ratio` parameter, and
some endpoints reject a reference whose dimensions differ from the target size
instead of resizing it (Sora 2: *"The image must match the target video's
resolution"*).

## The order to do it in

1. Pick the endpoint, look at what it adds — turn off the expanders if you are
   writing the prompt yourself.
2. Write the shot: subject + action beats + light + lens + palette.
3. Put resolution, aspect, duration and negatives in the parameters, not the prose.
4. Check the prompt against the checklist before you spend.
5. One variable per generation. Change the camera move *or* the light, not both,
   or you learn nothing about which one did it.

## What this checklist cannot do

It is a checklist, not a critic. It has no model behind it and no opinion about
your idea — it will not tell you the concept is boring, and a higher score does not
mean a better video. It only catches the documented mistakes, so that the
generation you pay for is testing your idea instead of your phrasing.
