"""The built-in preset catalogue for the studio.

A preset is a shot brief, not a keyword list. Each entry names the subject, the
light, the framing, the surface and the finish, so somebody who knows what they
want can pick a starting point and change one or two things instead of writing a
prompt from nothing.

The vocabulary here is the real one defined in `studio.adapters.base`: every mode
named below exists in IMAGE_MODES or VIDEO_MODES, every camera move exists in
CAMERA_MOVES, and every reference role exists in REFERENCE_ROLES. A preset may not
name a capability the platform cannot request, because a preset pointing at a mode
a model does not have is a button that fails after the user has typed everything.

This module is data only. It reads no files, opens no sockets, and imports nothing
beyond the typing helpers below.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

#: Image categories, in the order the studio shows them.
IMAGE_CATEGORIES: Tuple[str, ...] = (
    "portraiture",
    "product",
    "fashion-editorial",
    "food",
    "architecture",
    "interior",
    "landscape",
    "urban",
    "automotive",
    "wildlife",
    "macro",
    "editorial-illustration",
    "poster-graphic",
    "logo-mark",
    "concept-art",
    "character-design",
    "texture-pattern",
    "3d-render",
    "abstract",
    "restoration",
)

#: Video categories, in the order the studio shows them.
VIDEO_CATEGORIES: Tuple[str, ...] = (
    "cinematic-scene",
    "product-film",
    "fashion-film",
    "music-video",
    "social-short",
    "explainer",
    "documentary",
    "aerial",
    "timelapse",
    "action-sport",
    "dialogue-scene",
    "landscape-drone",
    "food-film",
    "automotive-film",
    "character-animation",
    "abstract-loop",
    "title-sequence",
)

CATEGORY_LABELS: Dict[str, str] = {
    "portraiture": "Portraiture",
    "product": "Product photography",
    "fashion-editorial": "Fashion editorial",
    "food": "Food photography",
    "architecture": "Architecture",
    "interior": "Interiors",
    "landscape": "Landscape",
    "urban": "Urban and street",
    "automotive": "Automotive",
    "wildlife": "Wildlife",
    "macro": "Macro",
    "editorial-illustration": "Editorial illustration",
    "poster-graphic": "Poster and graphic design",
    "logo-mark": "Logos and marks",
    "concept-art": "Concept art",
    "character-design": "Character design",
    "texture-pattern": "Texture and pattern",
    "3d-render": "3D render",
    "abstract": "Abstract",
    "restoration": "Restoration",
    "cinematic-scene": "Cinematic scene",
    "product-film": "Product film",
    "fashion-film": "Fashion film",
    "music-video": "Music video",
    "social-short": "Social short",
    "explainer": "Explainer",
    "documentary": "Documentary",
    "aerial": "Aerial",
    "timelapse": "Timelapse",
    "action-sport": "Action sport",
    "dialogue-scene": "Dialogue scene",
    "landscape-drone": "Landscape drone",
    "food-film": "Food film",
    "automotive-film": "Automotive film",
    "character-animation": "Character animation",
    "abstract-loop": "Abstract loop",
    "title-sequence": "Title sequence",
}


# ---------------------------------------------------------------------------
# Image presets
# ---------------------------------------------------------------------------

_IMAGE_PRESETS: Tuple[dict, ...] = (
    {
        "key": "image.portraiture.soft-window-light",
        "name": "Soft window light portrait",
        "modality": "image",
        "category": "portraiture",
        "description": "A chest-up portrait lit by one window, for headshots and profile pictures that need believable skin.",
        "prompt_template": (
            "Portrait of {subject} seated on a low wooden stool against a bare plaster wall, "
            "single north-facing window at camera left with a white card bouncing into the near "
            "cheek, 85 mm at f/2.8, chest-up framing with the eyes on the upper third, shadow "
            "falling softly across the far side of the face, neutral grade, pores and fine skin "
            "texture intact, no retouching."
        ),
        "negative_template": "waxy skin, plastic sheen, extra fingers, warped ears, beauty filter blur, watermark, blown highlights",
        "aspect_ratio": "4:5",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.5, "steps": 30, "sampler": "dpmpp_2m"},
        "camera": {},
        "reference_requirements": [
            {"role": "main_subject", "required": False, "note": "A photo of the person, to hold the face steady across a set."},
            {"role": "lighting", "required": False, "note": "A reference frame with the window-to-fill ratio you want."},
        ],
        "defaults": {"cfg": 4.5, "strength": 0.5, "denoise": 0.55},
        "tags": ["portrait", "window-light", "natural"],
        "modes": ["text_to_image", "image_to_image", "reference_guided"],
    },
    {
        "key": "image.portraiture.environmental-maker",
        "name": "Environmental maker portrait",
        "modality": "image",
        "category": "portraiture",
        "description": "A three-quarter portrait of someone in their own workspace, for profiles and press that need context around the person.",
        "prompt_template": (
            "Environmental portrait of {maker} standing at their workbench in a {workspace}, "
            "tools and half-finished work arranged out of focus behind them, light from a high "
            "window at camera right, 35 mm at f/4, waist-up framing, subject looking just past "
            "the lens, mixed daylight and one warm practical lamp, low contrast cool grade with "
            "visible grain."
        ),
        "negative_template": "studio backdrop, corporate headshot lighting, extra limbs, warped hands, floating tools, watermark",
        "aspect_ratio": "3:2",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 32},
        "camera": {},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo of the workspace so the set dressing matches."},
            {"role": "main_subject", "required": False, "note": "A photo of the person for facial likeness."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.5},
        "tags": ["portrait", "environmental", "documentary"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "multi_reference"],
    },
    {
        "key": "image.product.seamless-sweep",
        "name": "Seamless sweep product",
        "modality": "image",
        "category": "product",
        "description": "A single object on an unbroken paper sweep, for catalogue images that drop straight into a layout.",
        "prompt_template": (
            "{product} centred on a seamless {colour} paper sweep, one large softbox directly "
            "overhead at forty-five degrees with a white card filling from camera left, 100 mm "
            "macro at f/11, three-quarter view at product height, contact shadow grounding the "
            "base, clean edges, matte finish, no props."
        ),
        "negative_template": "cluttered background, harsh specular hotspot, dust, fingerprints, warped label text, watermark",
        "aspect_ratio": "1:1",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.0, "steps": 28},
        "camera": {},
        "reference_requirements": [
            {"role": "product", "required": True, "note": "A photo of the actual object; the shape and label must survive."},
        ],
        "defaults": {"cfg": 4.0, "strength": 0.65, "denoise": 0.6},
        "tags": ["product", "studio", "catalogue"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "background_replace", "background_remove"],
    },
    {
        "key": "image.product.stone-topdown",
        "name": "Product on honed stone",
        "modality": "image",
        "category": "product",
        "description": "A straight-down hero shot on honed stone, for editorial and social product images with texture under the object.",
        "prompt_template": (
            "{product} placed off-centre on a honed {stone} slab, shot straight down from one "
            "point two metres, one gridded light at camera left raking across the surface, 50 mm "
            "at f/8, small hard shadow to camera right, a folded linen edge just inside frame, "
            "warm neutral grade, stone pores visible."
        ),
        "negative_template": "flat even lighting, floating object, yellow cast, duplicated product, warped text, watermark",
        "aspect_ratio": "4:5",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.5, "steps": 30},
        "camera": {},
        "reference_requirements": [
            {"role": "product", "required": True, "note": "A photo of the object, ideally on a plain surface."},
            {"role": "color_palette", "required": False, "note": "A frame or swatch for the stone and linen tones."},
        ],
        "defaults": {"cfg": 4.5, "strength": 0.6},
        "tags": ["product", "topdown", "texture"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "multi_reference"],
    },
    {
        "key": "image.fashion-editorial.concrete-stairwell",
        "name": "Concrete stairwell editorial",
        "modality": "image",
        "category": "fashion-editorial",
        "description": "A full-length fashion frame in raw architecture, for lookbooks and magazine spreads.",
        "prompt_template": (
            "Full-length fashion frame of {model} standing on the landing of a raw board-marked "
            "concrete stairwell wearing {garment}, daylight falling from a skylight three floors "
            "up, 50 mm at f/2.5, slight low angle, subject on the right third with the stair rail "
            "running out of frame left, cool grey grade, cloth weight and seams legible."
        ),
        "negative_template": "extra fingers, mangled hands, floating feet, warped fabric, symmetrical face, garbled print, watermark",
        "aspect_ratio": "2:3",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 32},
        "camera": {},
        "reference_requirements": [
            {"role": "main_subject", "required": False, "note": "A photo of the model for likeness and body shape."},
            {"role": "style", "required": False, "note": "A reference lookbook frame for the grade and styling."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.55},
        "tags": ["fashion", "editorial", "architecture"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "style_transfer"],
    },
    {
        "key": "image.fashion-editorial.colour-block-studio",
        "name": "Colour block studio",
        "modality": "image",
        "category": "fashion-editorial",
        "description": "A hard-flash full-length against a painted backdrop, for campaign images with flat graphic punch.",
        "prompt_template": (
            "Full-length studio frame of a model in {outfit} against a painted {backdrop} "
            "backdrop, single bare flash head at camera left forty-five degrees, hard shadow "
            "thrown onto the paper, 70 mm at f/8, subject centred with a hand's width of backdrop "
            "at each edge, saturated flat grade, crisp edge on the silhouette."
        ),
        "negative_template": "soft beauty lighting, gradient backdrop, motion blur, extra limbs, garbled print, watermark",
        "aspect_ratio": "4:5",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.5, "steps": 30},
        "camera": {},
        "reference_requirements": [
            {"role": "color_palette", "required": False, "note": "Swatches for the backdrop and garment colours."},
            {"role": "composition", "required": False, "note": "A frame showing the pose and the space around the figure."},
        ],
        "defaults": {"cfg": 5.5, "strength": 0.6},
        "tags": ["fashion", "studio", "flash"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "variations"],
    },
    {
        "key": "image.food.overhead-table",
        "name": "Overhead table spread",
        "modality": "image",
        "category": "food",
        "description": "A straight-down spread of several dishes on a wooden table, for menus, recipe headers and features.",
        "prompt_template": (
            "Overhead spread of {dish} with two smaller side plates on a dark oak table, shot "
            "straight down from one point five metres, soft daylight from a window at frame left "
            "falling across the table, 35 mm at f/8, linen napkin and two mismatched forks just "
            "inside frame, warm neutral grade, crumbs and oil sheen left in place."
        ),
        "negative_template": "plastic looking food, radioactive saturation, impossible geometry, duplicated cutlery, watermark",
        "aspect_ratio": "1:1",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.5, "steps": 30},
        "camera": {},
        "reference_requirements": [
            {"role": "main_subject", "required": False, "note": "A photo of the finished dish so the garnish and plating match."},
        ],
        "defaults": {"cfg": 4.5, "strength": 0.55},
        "tags": ["food", "topdown", "table"],
        "modes": ["text_to_image", "image_to_image", "reference_guided"],
    },
    {
        "key": "image.food.side-window-hero",
        "name": "Side window food hero",
        "modality": "image",
        "category": "food",
        "description": "A low single-dish hero with steam and a dark falloff, for banners and cookbook pages.",
        "prompt_template": (
            "Single bowl of {dish} on a worn stoneware plate, window light at camera right raking "
            "across the surface, a dark wooden board falling into shadow behind, 90 mm at f/4, "
            "low three-quarter angle at plate height, steam catching the light, one scatter of "
            "herbs and a spoon handle out of focus at the edge, deep warm grade."
        ),
        "negative_template": "flat overhead lighting, plastic food, glowing steam, spilled sauce, warped text, watermark",
        "aspect_ratio": "3:2",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.5, "steps": 32},
        "camera": {},
        "reference_requirements": [
            {"role": "main_subject", "required": False, "note": "A photo of the dish for colour and portion size."},
            {"role": "lighting", "required": False, "note": "A frame with the hard side light you want."},
        ],
        "defaults": {"cfg": 4.5, "strength": 0.55},
        "tags": ["food", "window-light", "hero"],
        "modes": ["text_to_image", "image_to_image", "reference_guided"],
    },
    {
        "key": "image.architecture.blue-hour-facade",
        "name": "Blue hour facade",
        "modality": "image",
        "category": "architecture",
        "description": "A two-point elevation of a building at dusk with the interior lights on, for portfolio and press plates.",
        "prompt_template": (
            "Two-point elevation of {building} shot from street level across an empty road, deep "
            "blue hour sky, interior lights warm behind the glazing, 24 mm tilt-shift with "
            "verticals held straight at f/8, facade filling the frame with margin at each edge, "
            "wet asphalt carrying the window reflections, cool shadows with warm accents."
        ),
        "negative_template": "converging verticals, fisheye distortion, cars in frame, noise bands, duplicated windows, watermark",
        "aspect_ratio": "3:2",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 32},
        "camera": {},
        "reference_requirements": [
            {"role": "environment", "required": True, "note": "A photo of the building; the massing and glazing rhythm must match."},
            {"role": "composition", "required": False, "note": "A frame showing the camera position and crop."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.7},
        "tags": ["architecture", "blue-hour", "exterior"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "outpaint"],
    },
    {
        "key": "image.architecture.concrete-colonnade",
        "name": "Raking light concrete detail",
        "modality": "image",
        "category": "architecture",
        "description": "An axial detail of concrete structure with low sun across the surface, for material studies and detail pages.",
        "prompt_template": (
            "Axial view down a board-marked concrete colonnade of {building}, low late sun "
            "raking from camera left so every form tie and grain casts a long shadow, 35 mm at "
            "f/9 focused on the mid-ground, repeating piers converging on a bright opening, "
            "mono grade with a warm shadow tint, dust in the air."
        ),
        "negative_template": "smooth plastic concrete, warped lines, cluttered foreground, HDR halos, text, watermark",
        "aspect_ratio": "3:2",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 30},
        "camera": {},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo of the structure so the pier spacing is right."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.6},
        "tags": ["architecture", "material", "daylight"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "style_transfer"],
    },
    {
        "key": "image.interior.living-room-north-light",
        "name": "Living room, north light",
        "modality": "image",
        "category": "interior",
        "description": "A wide interior frame lit only by a large window, for listings and design portfolios.",
        "prompt_template": (
            "Wide interior of a {room} photographed from the doorway, tall north-facing window "
            "at frame left, 16 mm at f/8 on a tripod at one point four metres with verticals "
            "corrected, furniture arranged so the window wall leads into the room, a ladder-back "
            "chair and a low table in the mid-ground, white walls reading neutral rather than "
            "blue, floor texture resolved."
        ),
        "negative_template": "converging verticals, magenta walls, blown windows, doubled furniture, dead empty staging, watermark",
        "aspect_ratio": "3:2",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.5, "steps": 30},
        "camera": {},
        "reference_requirements": [
            {"role": "environment", "required": True, "note": "A photo or floor plan of the actual room."},
        ],
        "defaults": {"cfg": 4.5, "strength": 0.65},
        "tags": ["interior", "daylight", "wide"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "outpaint"],
    },
    {
        "key": "image.interior.corridor-evening",
        "name": "Hotel corridor at evening",
        "modality": "image",
        "category": "interior",
        "description": "A one-point corridor with warm practical light, for hospitality brands and mood boards.",
        "prompt_template": (
            "One-point perspective down a {corridor} of a hotel at night, warm wall sconces "
            "repeating along the right wall, 24 mm at f/5.6, camera at one point five metres on "
            "the centre line, carpet texture fading toward the far door, a service cart just out "
            "of focus at frame left, deep shadowed ceiling, warm amber grade with cool window "
            "light at the far end."
        ),
        "negative_template": "flat fluorescent lighting, warped door frames, duplicated sconces, noise, text, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 32},
        "camera": {},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo of the corridor for proportions and finishes."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.6},
        "tags": ["interior", "evening", "corridor"],
        "modes": ["text_to_image", "image_to_image", "reference_guided"],
    },
    {
        "key": "image.landscape.ridge-golden-hour",
        "name": "Ridge at golden hour",
        "modality": "image",
        "category": "landscape",
        "description": "A layered mountain ridge in low warm sun, for prints, travel features and openers.",
        "prompt_template": (
            "Layered ridges of {range} seen from a higher shoulder, low sun from camera right "
            "raking the near slope and leaving the valleys in cool shade, 70 mm at f/11, camera "
            "at chest height on a tripod, foreground grass in the lower third, three distinct "
            "depth planes, natural warm grade with honest shadow detail."
        ),
        "negative_template": "oversaturated sky, HDR halos, duplicated ridgelines, lens flare overload, warped horizon, watermark",
        "aspect_ratio": "3:2",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.5, "steps": 34},
        "camera": {},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo of the actual location for the terrain shape."},
        ],
        "defaults": {"cfg": 4.5, "strength": 0.5},
        "tags": ["landscape", "golden-hour", "mountains"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "outpaint"],
    },
    {
        "key": "image.landscape.fog-forest-layers",
        "name": "Fog forest layers",
        "modality": "image",
        "category": "landscape",
        "description": "A cool quiet forest with fog separating the depth planes, for calm editorial and backgrounds.",
        "prompt_template": (
            "Stand of {trees} seen through three bands of morning fog, nearest trunks sharp at "
            "the frame edges and darker, second row softened, third row nearly dissolved, 135 mm "
            "at f/5.6, camera at eye height, a thin path entering bottom centre, cool "
            "desaturated grade, no visible sun."
        ),
        "negative_template": "sunbeams overload, saturated green, sharp background, heavy vignette, text, watermark",
        "aspect_ratio": "4:5",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.5, "steps": 30},
        "camera": {},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo of the location for species and ground cover."},
        ],
        "defaults": {"cfg": 4.5, "strength": 0.55},
        "tags": ["landscape", "fog", "forest"],
        "modes": ["text_to_image", "image_to_image", "reference_guided"],
    },
    {
        "key": "image.urban.rain-corner-night",
        "name": "Rain corner at night",
        "modality": "image",
        "category": "urban",
        "description": "A wet street corner under neon and sodium light, for city stories and album art.",
        "prompt_template": (
            "Street corner in {city} at night in light rain, low camera at one point two metres "
            "looking across the crossing, neon and sodium signage reflecting in the wet asphalt, "
            "35 mm at f/1.8 so passing figures blur, a lit shop window anchoring the left edge, "
            "mixed teal and amber grade, grain kept."
        ),
        "negative_template": "clean dry street, purple haze everywhere, illegible signage, distorted limbs, watermark",
        "aspect_ratio": "3:2",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 32},
        "camera": {},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo of the street so the signage and furniture are local."},
            {"role": "color_palette", "required": False, "note": "A frame with the light mix you want."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.6},
        "tags": ["urban", "night", "rain"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "style_transfer"],
    },
    {
        "key": "image.urban.rooftop-blue-hour",
        "name": "Rooftop at blue hour",
        "modality": "image",
        "category": "urban",
        "description": "A rooftop view over a lit city at dusk, for city guides and title backgrounds.",
        "prompt_template": (
            "View from a {city} rooftop just after sunset, sky still blue at the top of frame and "
            "warm at the horizon, office windows lighting up in the mid-ground, 28 mm at f/8 on a "
            "tripod, low parapet wall running across the lower fifth, air haze softening the far "
            "towers, balanced grade with no crushed blacks."
        ),
        "negative_template": "midday sky, extreme HDR, streaked stars, tilted horizon, duplicated towers, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 34},
        "camera": {},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo of the skyline so the building stock is right."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.55},
        "tags": ["urban", "blue-hour", "skyline"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "outpaint"],
    },
    {
        "key": "image.automotive.studio-hero-static",
        "name": "Automotive studio hero",
        "modality": "image",
        "category": "automotive",
        "description": "A car in a dark studio with long strip highlights, for dealer and campaign hero images.",
        "prompt_template": (
            "{car} in three-quarter front view inside a dark studio, an overhead strip softbox "
            "running the length of the body and two upright strip lights defining the shoulder "
            "line, 85 mm at f/8, camera at bonnet height, gloss black floor with a soft "
            "reflection, deep shadow background, neutral grade with the paint colour held true."
        ),
        "negative_template": "distorted proportions, melted grille, wrong badges, rainbow reflections, duplicated wheels, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 34},
        "camera": {},
        "reference_requirements": [
            {"role": "product", "required": True, "note": "A photo of the actual vehicle; body shape and badges must be kept."},
            {"role": "lighting", "required": False, "note": "A reference of the strip-light reflection pattern."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.7},
        "tags": ["automotive", "studio", "reflections"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "background_replace"],
    },
    {
        "key": "image.automotive.desert-road-ambient",
        "name": "Car on a desert road",
        "modality": "image",
        "category": "automotive",
        "description": "A car held still on an empty road at low sun, for lifestyle and travel automotive work.",
        "prompt_template": (
            "{car} parked on the shoulder of an empty desert highway, low sun behind camera left "
            "throwing a long shadow across the tarmac, 50 mm at f/4 from a low position at wheel "
            "height, a distant ridge line across the top third, heat haze on the far road, dust "
            "on the lower panels kept, warm arid grade with an honest sky."
        ),
        "negative_template": "showroom-clean body, duplicated wheels, bent panels, fake lens flare, warped badges, watermark",
        "aspect_ratio": "3:2",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.5, "steps": 30},
        "camera": {},
        "reference_requirements": [
            {"role": "product", "required": True, "note": "A photo of the vehicle for shape and trim."},
            {"role": "environment", "required": False, "note": "A photo of the location so the terrain matches."},
        ],
        "defaults": {"cfg": 4.5, "strength": 0.6},
        "tags": ["automotive", "lifestyle", "desert"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "multi_reference"],
    },
    {
        "key": "image.wildlife.long-lens-portrait",
        "name": "Long lens wildlife portrait",
        "modality": "image",
        "category": "wildlife",
        "description": "A tight animal portrait at eye level with the background thrown out, for species features.",
        "prompt_template": (
            "Head and shoulders of a {animal} at eye level, 600 mm at f/5.6, early morning light "
            "from camera right, background scrub falling into soft green blur, focus on the near "
            "eye with the whiskers sharp, framing leaves margin above the ears, natural colour "
            "with no saturation lift, a single catchlight in the eye."
        ),
        "negative_template": "extra eyes, deformed muzzle, human-like features, oversharpened fur, double catchlights, watermark",
        "aspect_ratio": "3:2",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.5, "steps": 34},
        "camera": {},
        "reference_requirements": [
            {"role": "main_subject", "required": False, "note": "A photo of the species for markings and proportions."},
        ],
        "defaults": {"cfg": 4.5, "strength": 0.5},
        "tags": ["wildlife", "telephoto", "animal"],
        "modes": ["text_to_image", "image_to_image", "reference_guided"],
    },
    {
        "key": "image.wildlife.habitat-wide",
        "name": "Wildlife in habitat",
        "modality": "image",
        "category": "wildlife",
        "description": "A wide frame with the animal small inside its landscape, for conservation and magazine openers.",
        "prompt_template": (
            "Wide habitat frame of a {animal} moving through {habitat}, the animal on the right "
            "third and small in frame, 200 mm at f/8, late afternoon sun from camera left with "
            "long grass shadows, the terrain reading as one continuous plane from foreground to "
            "ridge, muted natural grade, space for a caption across the left third."
        ),
        "negative_template": "animal centred and oversized, zoo enclosure, saturated grass, duplicated animals, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.5, "steps": 30},
        "camera": {},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo of the habitat for terrain and vegetation."},
        ],
        "defaults": {"cfg": 4.5, "strength": 0.5},
        "tags": ["wildlife", "habitat", "wide"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "outpaint"],
    },
    {
        "key": "image.macro.dew-insect-stack",
        "name": "Focus-stacked insect macro",
        "modality": "image",
        "category": "macro",
        "description": "A close insect study with the whole body in focus, for nature pages and teaching material.",
        "prompt_template": (
            "Macro study of a {insect} resting on {surface}, 100 mm macro at f/5.6 with a "
            "twenty-frame focus stack so the antennae and the far wing are both sharp, ring "
            "light diffused through a paper hood, backlit dew catching the light along the body, "
            "the edge of the surface entering at frame bottom left, no background clutter."
        ),
        "negative_template": "blurred antennae, stacking halos, plastic chitin, duplicated legs, speckle noise, watermark",
        "aspect_ratio": "4:5",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.0, "steps": 36},
        "camera": {},
        "reference_requirements": [
            {"role": "main_subject", "required": False, "note": "A photo of the specimen to keep the anatomy right."},
        ],
        "defaults": {"cfg": 4.0, "strength": 0.5},
        "tags": ["macro", "insect", "focus-stack"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "upscale"],
    },
    {
        "key": "image.macro.material-weave-detail",
        "name": "Material weave detail",
        "modality": "image",
        "category": "macro",
        "description": "A raking-light close-up of a fabric or surface, for material sheets and product storytelling.",
        "prompt_template": (
            "Extreme close-up of {material}, 60 mm macro at f/8, low raking light from the right "
            "at about ten degrees above the surface so every thread and imperfection casts a "
            "shadow, frame filled edge to edge with the grain running on a slight diagonal, one "
            "fold rising through the middle, neutral grade, no vignette."
        ),
        "negative_template": "flat frontal lighting, moire banding, visible repeat seam, plastic texture, text, watermark",
        "aspect_ratio": "1:1",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.0, "steps": 30},
        "camera": {},
        "reference_requirements": [
            {"role": "style", "required": False, "note": "A close-up of the material so the weave reads true."},
        ],
        "defaults": {"cfg": 4.0, "strength": 0.6},
        "tags": ["macro", "material", "texture"],
        "modes": ["text_to_image", "image_to_image", "style_transfer", "enhance"],
    },
    {
        "key": "image.editorial-illustration.flat-spot",
        "name": "Flat spot illustration",
        "modality": "image",
        "category": "editorial-illustration",
        "description": "A small flat-colour illustration for an article, with room for the heading beside it.",
        "prompt_template": (
            "Flat vector-style spot illustration of {idea} in a two-colour palette of {colour} "
            "and off-white plus one accent, bold simple shapes with no gradients and no outlines, "
            "subject centred with generous empty space on the left for a headline, crisp edges, "
            "slight paper grain, editorial rather than decorative."
        ),
        "negative_template": "photorealism, gradients, drop shadows, cluttered detail, illegible text, watermark",
        "aspect_ratio": "1:1",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 28},
        "camera": {},
        "reference_requirements": [
            {"role": "style", "required": False, "note": "An existing illustration showing the palette and line weight."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.6},
        "tags": ["illustration", "flat", "editorial"],
        "modes": ["text_to_image", "image_to_image", "style_transfer", "reference_guided"],
    },
    {
        "key": "image.editorial-illustration.gouache-explainer",
        "name": "Gouache explainer panel",
        "modality": "image",
        "category": "editorial-illustration",
        "description": "A painted panel that explains one step of a process, for features and explainers.",
        "prompt_template": (
            "Gouache painting of {concept} as a single explainer panel, visible brush strokes "
            "and paper tooth, three objects arranged left to right showing the change, muted "
            "ochre and slate palette with one warm accent on the key object, soft off-white "
            "ground, no lettering, painterly edges left slightly rough."
        ),
        "negative_template": "digital airbrush, neon colours, busy background, tiny unreadable labels, frame border, watermark",
        "aspect_ratio": "4:3",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 32},
        "camera": {},
        "reference_requirements": [
            {"role": "style", "required": False, "note": "A reference painting for the brushwork and palette."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.6},
        "tags": ["illustration", "gouache", "explainer"],
        "modes": ["text_to_image", "image_to_image", "style_transfer"],
    },
    {
        "key": "image.poster-graphic.type-led-gig",
        "name": "Type-led gig poster",
        "modality": "image",
        "category": "poster-graphic",
        "description": "A poster built around one large headline, for gigs, screenings and events.",
        "prompt_template": (
            "Poster for {event} built around one oversized headline in a condensed grotesque, "
            "stacked in three lines across the upper half, a single photographic element of "
            "{subject} cropped hard along the bottom edge, two-colour {colour} and off-white "
            "print with visible halftone dots and a slightly misregistered second pass, a small "
            "block of details bottom left, print-ready margins."
        ),
        "negative_template": "garbled letterforms, warped type, too many fonts, rainbow gradients, stray text, watermark",
        "aspect_ratio": "2:3",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.5, "steps": 32},
        "camera": {},
        "reference_requirements": [
            {"role": "style", "required": False, "note": "An existing poster showing the type treatment and print feel."},
            {"role": "main_subject", "required": False, "note": "The photographic element to place on the poster."},
        ],
        "defaults": {"cfg": 5.5, "strength": 0.6},
        "tags": ["poster", "typography", "print"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "style_transfer"],
    },
    {
        "key": "image.poster-graphic.swiss-grid",
        "name": "Swiss grid print",
        "modality": "image",
        "category": "poster-graphic",
        "description": "A restrained grid composition of photograph and rules, for programme covers and cultural posters.",
        "prompt_template": (
            "Programme cover for {event} on an eight-column grid, one black-and-white photograph "
            "of {subject} cropped to a tall rectangle in the left five columns, three horizontal "
            "rules and a flush-left title in a neo-grotesque below it, generous white space, "
            "paper stock visible in the flat scan, one red accent mark, nothing decorative."
        ),
        "negative_template": "centred layout, decorative swirls, shadow on type, low contrast, misspelled words, watermark",
        "aspect_ratio": "2:3",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 30},
        "camera": {},
        "reference_requirements": [
            {"role": "main_subject", "required": False, "note": "The photograph that goes inside the grid."},
            {"role": "composition", "required": False, "note": "A layout reference for the grid proportions."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.6},
        "tags": ["poster", "grid", "minimal"],
        "modes": ["text_to_image", "image_to_image", "reference_guided"],
    },
    {
        "key": "image.logo-mark.geometric-monogram",
        "name": "Geometric monogram",
        "modality": "image",
        "category": "logo-mark",
        "description": "A single-colour monogram built from clean geometry, for a first mark to redraw and refine.",
        "prompt_template": (
            "Single-colour vector monogram of the letters {letters} built from one continuous "
            "stroke and a perfect circle, geometric construction with consistent stroke weight "
            "and squared terminals, centred on a plain off-white field with even margin on all "
            "four sides, flat black, no gradient, no shadow, drawn to survive at sixteen pixels."
        ),
        "negative_template": "gradients, bevels, drop shadow, hairline strokes, 3d perspective, extra letters, watermark",
        "aspect_ratio": "1:1",
        "model_preference": "",
        "parameters": {"cfg_scale": 6.0, "steps": 30},
        "camera": {},
        "reference_requirements": [
            {"role": "style", "required": False, "note": "A mark from the same family to match the stroke weight."},
        ],
        "defaults": {"cfg": 6.0, "strength": 0.7},
        "tags": ["logo", "monogram", "geometric"],
        "modes": ["text_to_image", "image_to_image", "variations", "style_transfer"],
    },
    {
        "key": "image.logo-mark.app-icon-flat",
        "name": "Flat app icon",
        "modality": "image",
        "category": "logo-mark",
        "description": "A squircle icon with one readable symbol, for an app store listing or favicon set.",
        "prompt_template": (
            "App icon on a squircle tile, one flat symbol of {symbol} centred and optically "
            "balanced, two flat fills of {colour} and one accent, no gradients and no inner "
            "shadow, the symbol occupying about sixty per cent of the tile with even padding, "
            "crisp vector edges, shown straight on at full size."
        ),
        "negative_template": "glossy skeuomorphism, long drop shadow, tiny detail, outlined text, perspective tilt, watermark",
        "aspect_ratio": "1:1",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.5, "steps": 28},
        "camera": {},
        "reference_requirements": [
            {"role": "style", "required": False, "note": "Icons from the same set so the family reads consistently."},
        ],
        "defaults": {"cfg": 5.5, "strength": 0.7},
        "tags": ["logo", "icon", "flat"],
        "modes": ["text_to_image", "image_to_image", "variations", "background_replace"],
    },
    {
        "key": "image.concept-art.environment-keyframe",
        "name": "Environment keyframe",
        "modality": "image",
        "category": "concept-art",
        "description": "A painted environment establishing a world and its light, for pitch decks and pre-production.",
        "prompt_template": (
            "Painted environment keyframe of {location}, human figures small in the foreground "
            "for scale, low sun breaking through {weather} from the upper right, wide "
            "twenty-four millimetre equivalent framing with the horizon on the lower third, cool "
            "shadows and warm highlights, loose brushwork in the sky and tighter detail on the "
            "near architecture, silhouettes readable at thumbnail size."
        ),
        "negative_template": "photographic realism, cluttered detail, unreadable silhouettes, floating elements, text, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.5, "steps": 34},
        "camera": {},
        "reference_requirements": [
            {"role": "composition", "required": False, "note": "A thumbnail sketch or block-out for the camera position."},
            {"role": "style", "required": False, "note": "A painting showing the brushwork you want to match."},
        ],
        "defaults": {"cfg": 5.5, "strength": 0.6},
        "tags": ["concept-art", "environment", "painterly"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "style_transfer", "outpaint"],
    },
    {
        "key": "image.concept-art.hardware-prop-sheet",
        "name": "Hardware prop sheet",
        "modality": "image",
        "category": "concept-art",
        "description": "One invented object from three angles on a neutral sheet, for art departments and model sheets.",
        "prompt_template": (
            "Prop sheet of {object} on a neutral grey field, three views side by side at the same "
            "scale: front, side and three-quarter, soft even studio light with a faint contact "
            "shadow, flat rendering with the material callouts left to the layout pass, "
            "consistent proportions across the views, muted industrial palette, no background "
            "detail."
        ),
        "negative_template": "inconsistent proportions between views, dramatic lighting, cluttered background, stray notes, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 32},
        "camera": {},
        "reference_requirements": [
            {"role": "style", "required": False, "note": "An existing prop sheet for the drawing convention."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.55},
        "tags": ["concept-art", "prop", "model-sheet"],
        "modes": ["text_to_image", "image_to_image", "variations"],
    },
    {
        "key": "image.character-design.turnaround-sheet",
        "name": "Character turnaround",
        "modality": "image",
        "category": "character-design",
        "description": "A four-view turnaround of one character at a fixed scale, for animation and game bibles.",
        "prompt_template": (
            "Character turnaround of {character} on a plain light grey field, four views in a row "
            "at identical scale: front, three-quarter, side and back, neutral standing pose with "
            "the arms slightly away from the body, flat even light with no cast shadow, the same "
            "costume detail in every view including seams and fastenings, clean line work with "
            "flat colour, a faint baseline under the feet."
        ),
        "negative_template": "costume changing between views, different heights, dramatic light, extra limbs, warped hands, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 34},
        "camera": {},
        "reference_requirements": [
            {"role": "character_identity", "required": False, "note": "A photo or sketch that fixes the face and build."},
            {"role": "style", "required": False, "note": "A sheet from the same production for the drawing style."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.6},
        "tags": ["character", "turnaround", "production"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "multi_reference"],
    },
    {
        "key": "image.character-design.costume-detail-board",
        "name": "Costume detail board",
        "modality": "image",
        "category": "character-design",
        "description": "Close studies of one costume's construction, for wardrobe build and continuity reference.",
        "prompt_template": (
            "Costume detail board for {character}, four close studies arranged on a neutral board: "
            "the shoulder seam, the cuff, the belt buckle and the boot, each drawn at the same "
            "scale with flat grey shadow, no annotation, the material difference between "
            "{fabric_a} and {fabric_b} clear in the surface rendering, muted production palette."
        ),
        "negative_template": "dramatic lighting, mixed drawing scales, illegible handwriting, warped stitching, glitter, watermark",
        "aspect_ratio": "3:2",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 30},
        "camera": {},
        "reference_requirements": [
            {"role": "character_identity", "required": False, "note": "The approved turnaround to keep the design consistent."},
            {"role": "style", "required": False, "note": "Existing boards for the layout convention."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.6},
        "tags": ["character", "costume", "production"],
        "modes": ["text_to_image", "image_to_image", "reference_guided"],
    },
    {
        "key": "image.texture-pattern.tiling-fabric",
        "name": "Tiling fabric weave",
        "modality": "image",
        "category": "texture-pattern",
        "description": "A seamless cloth texture for material libraries, 3D work and backgrounds.",
        "prompt_template": (
            "Seamless tiling texture of {fabric} at roughly life scale, shot straight on under "
            "flat even light so there is no shading and no vignette, threads running level, the "
            "pattern repeating cleanly at all four edges, neutral white balance, fine fibres "
            "resolved, no folds and no shadows."
        ),
        "negative_template": "visible seam, directional light, wrinkles, vignette, moire, colour cast, watermark",
        "aspect_ratio": "1:1",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.0, "steps": 28},
        "camera": {},
        "reference_requirements": [
            {"role": "style", "required": False, "note": "A cloth swatch so the weave scale is right."},
        ],
        "defaults": {"cfg": 4.0, "strength": 0.65},
        "tags": ["texture", "seamless", "fabric"],
        "modes": ["text_to_image", "image_to_image", "style_transfer", "enhance"],
    },
    {
        "key": "image.texture-pattern.weathered-surface",
        "name": "Weathered surface",
        "modality": "image",
        "category": "texture-pattern",
        "description": "An aged wall or panel surface for backdrops, mattes and overlay work.",
        "prompt_template": (
            "Flat-on photograph of {surface} worn by weather, paint flaking along one diagonal "
            "and revealing the layer beneath, even overcast light with no directional shadow, "
            "frame filled edge to edge with no objects, natural colour with honest dirt, "
            "scratches and hairline cracks kept, texture sharp enough to read at full size."
        ),
        "negative_template": "glossy new surface, hard shadow, vignette, decorative grunge overlay, graffiti, watermark",
        "aspect_ratio": "3:2",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.0, "steps": 30},
        "camera": {},
        "reference_requirements": [
            {"role": "style", "required": False, "note": "A photo of the surface, so the material and its age read true."},
        ],
        "defaults": {"cfg": 4.0, "strength": 0.6},
        "tags": ["texture", "weathered", "surface"],
        "modes": ["text_to_image", "image_to_image", "style_transfer", "upscale"],
    },
    {
        "key": "image.3d-render.product-visualisation",
        "name": "Product visualisation",
        "modality": "image",
        "category": "3d-render",
        "description": "A clean CGI product render for packaging mockups and pre-production decks.",
        "prompt_template": (
            "Three-dimensional render of {product} on a pale grey studio floor, one large area "
            "light overhead and a soft rim from behind, physically based materials with accurate "
            "roughness and a visible bevel on every edge, 50 mm equivalent at product height, "
            "soft ambient occlusion in the contact shadow, no post-processing bloom."
        ),
        "negative_template": "impossible reflections, plastic CGI sheen, floating object, aliased edges, render noise, watermark",
        "aspect_ratio": "1:1",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 32},
        "camera": {},
        "reference_requirements": [
            {"role": "product", "required": False, "note": "A photo or CAD view of the object for accurate proportions."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.65},
        "tags": ["3d", "product", "studio"],
        "modes": ["text_to_image", "image_to_image", "reference_guided", "background_replace"],
    },
    {
        "key": "image.3d-render.isometric-diorama",
        "name": "Isometric diorama",
        "modality": "image",
        "category": "3d-render",
        "description": "A small isometric world slice on a floating base, for explainers and landing pages.",
        "prompt_template": (
            "Isometric three-dimensional diorama of {scene} on a cut slab of earth, viewed from "
            "a true isometric angle, soft global illumination with one warm key from the upper "
            "left and a cool sky fill, clean shading with no visible noise, the slab edges "
            "showing soil layers, plain light background, tidy stylised proportions."
        ),
        "negative_template": "perspective distortion, noisy render, floating geometry, cluttered props, text, watermark",
        "aspect_ratio": "1:1",
        "model_preference": "",
        "parameters": {"cfg_scale": 5.0, "steps": 34},
        "camera": {},
        "reference_requirements": [
            {"role": "style", "required": False, "note": "An existing diorama for the shading and proportion style."},
        ],
        "defaults": {"cfg": 5.0, "strength": 0.6},
        "tags": ["3d", "isometric", "stylised"],
        "modes": ["text_to_image", "image_to_image", "style_transfer", "variations"],
    },
    {
        "key": "image.abstract.light-through-glass",
        "name": "Light through glass",
        "modality": "image",
        "category": "abstract",
        "description": "Refracted light and shadow on a plain surface, for backgrounds and cover art.",
        "prompt_template": (
            "Abstract study of {light} refracting through a thick glass object onto a {colour} "
            "plaster wall, hard-edged caustics and a chromatic fringe where the beam splits, "
            "50 mm at f/5.6 focused on the wall, the glass itself out of frame, two overlapping "
            "shadows with soft edges, deep colour held in the shadows."
        ),
        "negative_template": "digital noise texture, banding, oversaturated colour, lens flare, visible object edge, watermark",
        "aspect_ratio": "3:2",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.5, "steps": 30},
        "camera": {},
        "reference_requirements": [
            {"role": "color_palette", "required": False, "note": "A frame or swatch so the light colour matches the brand."},
        ],
        "defaults": {"cfg": 4.5, "strength": 0.55},
        "tags": ["abstract", "light", "caustics"],
        "modes": ["text_to_image", "style_transfer", "variations"],
    },
    {
        "key": "image.abstract.ink-in-water",
        "name": "Ink in water",
        "modality": "image",
        "category": "abstract",
        "description": "Suspended ink plumes photographed against a plain ground, for title cards and texture layers.",
        "prompt_template": (
            "Ink of {colour} dispersing in still water, photographed from the side against a "
            "plain {background} ground, hard backlight so the plume glows at its thin edges, "
            "100 mm macro at f/8, the plume entering from the top third and trailing down and "
            "right, no container edges in frame, motion held sharp."
        ),
        "negative_template": "visible tank edges, bubbles, muddy colour, digital smudge brush, text, watermark",
        "aspect_ratio": "4:5",
        "model_preference": "",
        "parameters": {"cfg_scale": 4.5, "steps": 32},
        "camera": {},
        "reference_requirements": [],
        "defaults": {"cfg": 4.5, "strength": 0.5},
        "tags": ["abstract", "ink", "texture"],
        "modes": ["text_to_image", "variations", "style_transfer"],
    },
    {
        "key": "image.restoration.faded-print-repair",
        "name": "Faded print repair",
        "modality": "image",
        "category": "restoration",
        "description": "Brings a faded or colour-shifted family photograph back to natural tones without changing the subject.",
        "prompt_template": (
            "Restore the supplied scan of {subject}: correct the overall cast so the skin reads "
            "natural, recover detail in the shadows and the highlights, remove dust and "
            "scratches on clean background areas only, keep the original grain and the original "
            "framing, and add no detail that is not already in the scan."
        ),
        "negative_template": "invented faces, sharpening halos, oversaturated colour, cropped edges, plastic skin, watermark",
        "aspect_ratio": "3:2",
        "model_preference": "",
        "parameters": {"cfg_scale": 3.5, "steps": 40},
        "camera": {},
        "reference_requirements": [
            {"role": "main_subject", "required": True, "note": "The scan to restore; this preset never invents content."},
        ],
        "defaults": {"cfg": 3.5, "strength": 0.35, "denoise": 0.3},
        "tags": ["restoration", "archive", "repair"],
        "modes": ["image_to_image", "enhance", "upscale", "inpaint"],
    },
    {
        "key": "image.restoration.scan-scratch-cleanup",
        "name": "Scan scratch cleanup",
        "modality": "image",
        "category": "restoration",
        "description": "Removes dust, creases and handling marks from a document or flat artwork scan.",
        "prompt_template": (
            "Clean up the supplied scan of {document}: remove the dust, hair and surface "
            "scratches, flatten the crease across the {area}, keep the paper fibre, the print "
            "texture and every line of original content exactly where it is, hold the original "
            "tonal range, and leave the page edges intact."
        ),
        "negative_template": "lost text, smudged letterforms, retyped content, invented borders, colour cast, watermark",
        "aspect_ratio": "4:5",
        "model_preference": "",
        "parameters": {"cfg_scale": 3.5, "steps": 40},
        "camera": {},
        "reference_requirements": [
            {"role": "main_subject", "required": True, "note": "The scan to clean; text and layout must be preserved."},
        ],
        "defaults": {"cfg": 3.5, "strength": 0.3, "denoise": 0.35},
        "tags": ["restoration", "document", "cleanup"],
        "modes": ["image_to_image", "inpaint", "enhance", "upscale"],
    },
)


# ---------------------------------------------------------------------------
# Video presets
# ---------------------------------------------------------------------------

_VIDEO_PRESETS: Tuple[dict, ...] = (
    {
        "key": "video.cinematic-scene.rain-street-reveal",
        "name": "Rain street reveal",
        "modality": "video",
        "category": "cinematic-scene",
        "description": "A slow push down a wet street that settles on one lit figure, for scene openers.",
        "prompt_template": (
            "Night street in {city} under steady rain, camera pushing forward at walking pace "
            "along the centre of the road toward a single figure standing under a shop awning, "
            "wet asphalt carrying the neon reflections, 35 mm anamorphic look at f/2 with "
            "shallow depth so the near bollards smear, sodium and teal mixed grade, rain visible "
            "in the backlight, the move settling with the figure centred."
        ),
        "negative_template": "flickering light, warped buildings, morphing faces, stuttering motion, text, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 6, "fps": 24, "motion_strength": 0.6},
        "camera": {"move": "push_in", "speed": "slow"},
        "reference_requirements": [
            {"role": "first_frame", "required": False, "note": "The establishing frame you want to start from."},
            {"role": "environment", "required": False, "note": "A photo of the street so the signage is local."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.6, "duration_seconds": 6},
        "tags": ["cinematic", "night", "rain"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "extend"],
    },
    {
        "key": "video.cinematic-scene.warehouse-walkthrough",
        "name": "Warehouse walkthrough",
        "modality": "video",
        "category": "cinematic-scene",
        "description": "A tracking shot that follows a character through an industrial space, for tension or arrival beats.",
        "prompt_template": (
            "Tracking shot following {character} from behind through a dim {space}, camera at "
            "chest height moving at the pace of the walk, hanging lamps passing overhead and "
            "flaring as they cross frame, 40 mm at f/2, dust in the beams, the character held on "
            "the same third of frame throughout, cold grade with warm lamp accents."
        ),
        "negative_template": "sliding feet, character drifting in frame, jittery camera, morphing background, text, watermark",
        "aspect_ratio": "2.39:1",
        "model_preference": "",
        "parameters": {"duration_seconds": 8, "fps": 24, "motion_strength": 0.55},
        "camera": {"move": "tracking", "speed": "medium"},
        "reference_requirements": [
            {"role": "character_identity", "required": False, "note": "A photo of the character for the silhouette and wardrobe."},
            {"role": "environment", "required": False, "note": "A photo of the location for the ceiling height and fittings."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.55, "duration_seconds": 8},
        "tags": ["cinematic", "tracking", "industrial"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "scene_continuation"],
    },
    {
        "key": "video.product-film.rotation-tabletop",
        "name": "Tabletop rotation",
        "modality": "video",
        "category": "product-film",
        "description": "A quarter-circle orbit around a product on a turntable, for web hero loops.",
        "prompt_template": (
            "{product} standing on a brushed metal turntable against a dark grey sweep, camera "
            "orbiting a quarter turn to the right at a constant radius, one overhead strip light "
            "and one vertical strip at camera left travelling down the body as the angle changes, "
            "85 mm at f/8, focus locked on the label, no background movement."
        ),
        "negative_template": "wobbling product, changing label text, flickering reflections, floating object, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 5, "fps": 30, "motion_strength": 0.4},
        "camera": {"move": "orbit", "speed": "slow"},
        "reference_requirements": [
            {"role": "product", "required": True, "note": "A photo of the object; shape, label and trim must stay fixed."},
        ],
        "defaults": {"cfg": 4.0, "motion_strength": 0.4, "duration_seconds": 5},
        "tags": ["product", "orbit", "studio"],
        "modes": ["image_to_video", "text_to_video", "first_frame", "extend"],
    },
    {
        "key": "video.product-film.pour-and-settle",
        "name": "Pour and settle",
        "modality": "video",
        "category": "product-film",
        "description": "A locked-off shot of liquid moving inside a container, for beverage and skincare films.",
        "prompt_template": (
            "Locked-off close shot of {liquid} pouring into {product} on a stone counter, the "
            "stream entering from the top left of frame, camera at glass height, hard side light "
            "so the falling liquid catches a bright edge, 100 mm macro at f/5.6, bubbles rising "
            "to the surface and settling, background falling to shadow, no camera movement."
        ),
        "negative_template": "rubber liquid, splash cut off midair, container warping, frozen motion, text, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 5, "fps": 30, "motion_strength": 0.35},
        "camera": {"move": "static", "speed": "slow"},
        "reference_requirements": [
            {"role": "product", "required": True, "note": "A photo of the container for shape and label."},
        ],
        "defaults": {"cfg": 4.0, "motion_strength": 0.35, "duration_seconds": 5},
        "tags": ["product", "liquid", "macro"],
        "modes": ["image_to_video", "text_to_video", "first_frame", "motion_transfer"],
    },
    {
        "key": "video.fashion-film.studio-turn",
        "name": "Studio turn",
        "modality": "video",
        "category": "fashion-film",
        "description": "A model turning slowly in a lit studio, for lookbook and e-commerce clips.",
        "prompt_template": (
            "Model in {outfit} turning a half turn in place on a {backdrop} studio sweep, camera "
            "locked on a 50 mm at f/4, one large softbox from camera left and a rim light from "
            "behind, cloth swinging and settling with the turn, eye line holding just past the "
            "lens, the soft shadow moving across the paper, flat clean grade."
        ),
        "negative_template": "warped face, extra fingers, cloth melting into skin, sliding feet, stuttering, watermark",
        "aspect_ratio": "9:16",
        "model_preference": "",
        "parameters": {"duration_seconds": 6, "fps": 30, "motion_strength": 0.45},
        "camera": {"move": "orbit", "speed": "slow"},
        "reference_requirements": [
            {"role": "character_identity", "required": False, "note": "A photo of the model to hold the face steady."},
            {"role": "product", "required": False, "note": "A garment photo so the cut and seams stay accurate."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.45, "duration_seconds": 6},
        "tags": ["fashion", "studio", "turn"],
        "modes": ["image_to_video", "text_to_video", "first_frame", "extend"],
    },
    {
        "key": "video.fashion-film.street-walk",
        "name": "Street walk",
        "modality": "video",
        "category": "fashion-film",
        "description": "A following shot of a model walking a real street, for campaign films.",
        "prompt_template": (
            "Following shot of a model in {outfit} walking toward camera along a {street}, "
            "camera retreating at walking pace at chest height, 35 mm at f/2.8 so the street "
            "stays busy behind them, late afternoon sun from frame left with long shadows "
            "crossing the pavement, cloth moving with the stride, warm neutral grade with grain."
        ),
        "negative_template": "sliding feet, crowd freezing, face changing, camera jitter, warped buildings, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 8, "fps": 24, "motion_strength": 0.5},
        "camera": {"move": "pull_out", "speed": "medium"},
        "reference_requirements": [
            {"role": "character_identity", "required": False, "note": "A photo of the model for likeness."},
            {"role": "environment", "required": False, "note": "A photo of the street so the location reads true."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.5, "duration_seconds": 8},
        "tags": ["fashion", "street", "walking"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "scene_continuation"],
    },
    {
        "key": "video.music-video.neon-hallway",
        "name": "Neon hallway",
        "modality": "video",
        "category": "music-video",
        "description": "A fast lateral pan through a lit corridor, for performance cutaways.",
        "prompt_template": (
            "Whip pan along a narrow corridor lined with {colour} neon tubing, camera sweeping "
            "left at speed so the tubes streak and the far end of the corridor snaps into focus "
            "on the stop, performer in silhouette against the lit end wall, 24 mm at f/2, heavy "
            "bloom on the tubes, deep shadows, saturated colour grade."
        ),
        "negative_template": "static camera, soft mush, tube colour shifting, lens smearing the subject, text, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 4, "fps": 24, "motion_strength": 0.8},
        "camera": {"move": "whip_pan", "speed": "fast"},
        "reference_requirements": [
            {"role": "color_palette", "required": False, "note": "A frame with the neon colour and bloom level."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.8, "duration_seconds": 4},
        "tags": ["music-video", "neon", "whip-pan"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "restyle"],
    },
    {
        "key": "video.music-video.silhouette-performance",
        "name": "Silhouette performance",
        "modality": "video",
        "category": "music-video",
        "description": "A slow-motion performance beat against a hard backlight, for chorus cuts.",
        "prompt_template": (
            "{performer} filmed in silhouette against a single hard backlight on a dark stage, "
            "camera locked at chest height, 85 mm at f/2, slow motion at quarter speed so hair "
            "and clothing trail, haze in the air catching the beam, arms rising through the "
            "light, flare controlled, high contrast grade with the floor crushed."
        ),
        "negative_template": "flat lighting, smeared limbs, flickering exposure, banding in the shadows, text, watermark",
        "aspect_ratio": "2.39:1",
        "model_preference": "",
        "parameters": {"duration_seconds": 5, "fps": 30, "motion_strength": 0.5},
        "camera": {"move": "slow_motion", "speed": "slow"},
        "reference_requirements": [
            {"role": "main_subject", "required": False, "note": "A photo of the performer for the silhouette shape."},
            {"role": "lighting", "required": False, "note": "A frame showing the backlight position and haze."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.5, "duration_seconds": 5},
        "tags": ["music-video", "silhouette", "slow-motion"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "motion_transfer"],
    },
    {
        "key": "video.social-short.hook-open",
        "name": "Vertical hook open",
        "modality": "video",
        "category": "social-short",
        "description": "A three-second vertical open that puts the product on screen immediately.",
        "prompt_template": (
            "Vertical shot opening tight on {product} held in a hand at chest height, camera "
            "pushing in gently as the hand turns the label toward the lens, bright daylight from "
            "a window at frame left, 28 mm at f/2.4, a plain {colour} wall behind, the subject "
            "filling the centre with the top of frame kept clear for captions, clean warm grade."
        ),
        "negative_template": "cropped subject, sideways framing, shaky camera, warped label, on-screen text, watermark",
        "aspect_ratio": "9:16",
        "model_preference": "",
        "parameters": {"duration_seconds": 3, "fps": 30, "motion_strength": 0.4},
        "camera": {"move": "push_in", "speed": "slow"},
        "reference_requirements": [
            {"role": "product", "required": True, "note": "A photo of the product so the label stays readable."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.4, "duration_seconds": 3},
        "tags": ["social", "vertical", "product"],
        "modes": ["image_to_video", "text_to_video", "first_frame", "extend"],
    },
    {
        "key": "video.social-short.recipe-beat",
        "name": "Vertical recipe beat",
        "modality": "video",
        "category": "social-short",
        "description": "One overhead cooking step filmed straight down, for recipe shorts cut in sequence.",
        "prompt_template": (
            "Overhead vertical shot straight down on a {colour} cutting board, hands adding "
            "{ingredient} to a hot pan in one continuous motion, camera locked at one point four "
            "metres, soft window light from frame left, 35 mm at f/5.6, steam rising out of the "
            "top of frame, ingredients arranged inside the centre square, warm grade."
        ),
        "negative_template": "camera drift, extra hands, food morphing, harsh overhead flash, on-screen text, watermark",
        "aspect_ratio": "9:16",
        "model_preference": "",
        "parameters": {"duration_seconds": 5, "fps": 30, "motion_strength": 0.45},
        "camera": {"move": "static", "speed": "medium"},
        "reference_requirements": [
            {"role": "composition", "required": False, "note": "An existing shot showing the overhead crop you want."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.45, "duration_seconds": 5},
        "tags": ["social", "recipe", "overhead"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "scene_continuation"],
    },
    {
        "key": "video.explainer.diagram-build",
        "name": "Diagram build",
        "modality": "video",
        "category": "explainer",
        "description": "A flat diagram assembling itself on a clean ground, for product and process explainers.",
        "prompt_template": (
            "Flat diagram on a plain off-white ground, {diagram} assembling itself in order: the "
            "frame first, then the nodes, then the connecting lines drawn left to right, camera "
            "locked and square to the page, even flat lighting with no shadow, line weight "
            "constant throughout, one accent colour reserved for the final element."
        ),
        "negative_template": "handheld drift, perspective skew, illegible labels, morphing shapes, glow effects, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 6, "fps": 25, "motion_strength": 0.3},
        "camera": {"move": "static", "speed": "slow"},
        "reference_requirements": [
            {"role": "composition", "required": False, "note": "A storyboard or the final diagram layout."},
            {"role": "style", "required": False, "note": "Existing brand illustrations for the line weight and palette."},
        ],
        "defaults": {"cfg": 4.0, "motion_strength": 0.3, "duration_seconds": 6},
        "tags": ["explainer", "diagram", "flat"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "extend"],
    },
    {
        "key": "video.explainer.object-cutaway",
        "name": "Object cutaway orbit",
        "modality": "video",
        "category": "explainer",
        "description": "A slow orbit around a cutaway model showing what sits inside a product.",
        "prompt_template": (
            "Three-dimensional cutaway model of {object} on a neutral grey ground, camera "
            "orbiting slowly to the left to reveal the internal stack of {parts}, the materials "
            "rendered at distinct roughness so each layer reads separately, one soft key from "
            "above and a cool fill, no labels on screen, an even render with no noise."
        ),
        "negative_template": "impossible geometry, layers merging, flickering shading, cluttered parts, text callouts, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 7, "fps": 30, "motion_strength": 0.4},
        "camera": {"move": "orbit", "speed": "slow"},
        "reference_requirements": [
            {"role": "product", "required": False, "note": "A CAD view or photo of the object for the internal layout."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.4, "duration_seconds": 7},
        "tags": ["explainer", "3d", "cutaway"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "extend"],
    },
    {
        "key": "video.documentary.hands-at-work",
        "name": "Hands at work",
        "modality": "video",
        "category": "documentary",
        "description": "A handheld close study of a craft process, for documentary inserts.",
        "prompt_template": (
            "Handheld close shot of {worker} working at {task}, camera at bench height with a "
            "small natural float, 50 mm at f/2.8 focused on the hands, window light from camera "
            "right and a warm practical lamp behind, sawdust or swarf visible on the bench, "
            "shallow depth so the workshop falls away, ungraded documentary colour."
        ),
        "negative_template": "gimbal smoothness, extra fingers, tool passing through the hand, harsh flash, warped hands, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 6, "fps": 25, "motion_strength": 0.5},
        "camera": {"move": "handheld", "speed": "slow"},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo of the workshop for the bench and tool set."},
            {"role": "main_subject", "required": False, "note": "A photo of the person for the hands and sleeves."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.5, "duration_seconds": 6},
        "tags": ["documentary", "handheld", "craft"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "scene_continuation"],
    },
    {
        "key": "video.documentary.interview-broll",
        "name": "Interview B-roll tilt",
        "modality": "video",
        "category": "documentary",
        "description": "A slow tilt from an object up to a seated subject, for interview cutaways.",
        "prompt_template": (
            "Slow tilt up from {object} on a table to {subject} seated by a window, camera on a "
            "tripod at seated eye height, 50 mm at f/2.8, soft daylight from the window at frame "
            "left with a gentle fill, background held out of focus, the move continuous and "
            "even across the whole clip, natural colour with light grain."
        ),
        "negative_template": "jerky tilt, hunting autofocus, subject sliding in frame, overexposed window, warped face, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 6, "fps": 25, "motion_strength": 0.35},
        "camera": {"move": "tilt", "speed": "slow"},
        "reference_requirements": [
            {"role": "main_subject", "required": False, "note": "A photo of the interviewee for likeness."},
            {"role": "environment", "required": False, "note": "A photo of the room for the window position."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.35, "duration_seconds": 6},
        "tags": ["documentary", "interview", "tilt"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "extend"],
    },
    {
        "key": "video.aerial.coastline-reveal",
        "name": "Coastline reveal",
        "modality": "video",
        "category": "aerial",
        "description": "A high forward flight that opens onto a coastline, for travel openers.",
        "prompt_template": (
            "Aerial forward flight over {coastline} at around a hundred and twenty metres, camera "
            "tilted down about thirty degrees, moving steadily toward the headland so the bay "
            "opens into frame, 24 mm equivalent, late afternoon sun from the left with the surf "
            "line bright against dark water, exposure held across the whole move, natural colour."
        ),
        "negative_template": "drone shake, spinning horizon, water freezing into texture, jello, warped cliffs, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 8, "fps": 30, "motion_strength": 0.55},
        "camera": {"move": "aerial", "speed": "medium"},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo or map of the coastline for the geography."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.55, "duration_seconds": 8},
        "tags": ["aerial", "coast", "travel"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "extend"],
    },
    {
        "key": "video.aerial.city-descent",
        "name": "City descent",
        "modality": "video",
        "category": "aerial",
        "description": "A descending crane move over rooftops toward one street, for city films.",
        "prompt_template": (
            "Crane descent over the rooftops of {city}, starting above the parapet line and "
            "dropping slowly toward a single {street} below, camera level with the horizon "
            "throughout, 35 mm equivalent, warm morning light from the east with long roof "
            "shadows, the street filling the frame by the end of the move, clear air with no "
            "haze banding."
        ),
        "negative_template": "camera tilt drift, zoom instead of travel, melting buildings, flickering exposure, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 7, "fps": 30, "motion_strength": 0.6},
        "camera": {"move": "crane_down", "speed": "slow"},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo of the district so the roofscape is real."},
            {"role": "first_frame", "required": False, "note": "The opening frame you want the descent to start from."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.6, "duration_seconds": 7},
        "tags": ["aerial", "city", "crane"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "extend"],
    },
    {
        "key": "video.timelapse.city-day-night",
        "name": "City day to night",
        "modality": "video",
        "category": "timelapse",
        "description": "A fixed camera compressed from afternoon into night over one skyline.",
        "prompt_template": (
            "Locked-off timelapse of the {city} skyline from a fixed rooftop position, clouds "
            "streaking overhead as the light moves from afternoon through sunset into night, "
            "office windows lighting up in sequence, traffic below rendering as light trails on "
            "the road, 24 mm at f/8, exposure held even across the whole transition."
        ),
        "negative_template": "camera drift, flickering brightness, jumping clouds, strobing lights, banded sky, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 8, "fps": 24, "motion_strength": 0.25},
        "camera": {"move": "static", "speed": "fast"},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo of the skyline from the chosen viewpoint."},
        ],
        "defaults": {"cfg": 4.0, "motion_strength": 0.25, "duration_seconds": 8},
        "tags": ["timelapse", "city", "day-night"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "extend"],
    },
    {
        "key": "video.timelapse.clouds-over-ridge",
        "name": "Clouds over a ridge",
        "modality": "video",
        "category": "timelapse",
        "description": "A fixed landscape frame with cloud shadow sweeping across it, for openers and backgrounds.",
        "prompt_template": (
            "Locked-off timelapse of {ridge} with cloud shadow sweeping across the slope, the "
            "clouds moving fast enough to read as a steady drift, 70 mm at f/11, three depth "
            "planes held sharp, the light shifting from flat to direct as the shadow passes, "
            "exposure steady, natural grade with no colour pumping."
        ),
        "negative_template": "camera shake, exposure flicker, boiling detail, colour pumping, trails on rock, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 8, "fps": 24, "motion_strength": 0.3},
        "camera": {"move": "static", "speed": "fast"},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo of the ridge for the terrain silhouette."},
        ],
        "defaults": {"cfg": 4.0, "motion_strength": 0.3, "duration_seconds": 8},
        "tags": ["timelapse", "landscape", "clouds"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "extend"],
    },
    {
        "key": "video.action-sport.skate-follow",
        "name": "Skate follow",
        "modality": "video",
        "category": "action-sport",
        "description": "A following shot tracking a rider through a street spot, for sport films.",
        "prompt_template": (
            "Following shot tracking {athlete} through a {spot}, camera moving at the same speed "
            "at knee height behind and slightly to the side, 16 mm at f/4 with a fast shutter so "
            "the wheels stay crisp, the background streaking past, low sun from behind throwing "
            "the rider's shadow ahead of them, high contrast grade with grain."
        ),
        "negative_template": "sliding wheels, rider floating, rubber limbs, camera through solid objects, stutter, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 6, "fps": 30, "motion_strength": 0.7},
        "camera": {"move": "tracking", "speed": "fast"},
        "reference_requirements": [
            {"role": "main_subject", "required": False, "note": "A photo of the athlete for the clothing and build."},
            {"role": "environment", "required": False, "note": "A photo of the spot for the obstacles and paving."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.7, "duration_seconds": 6},
        "tags": ["sport", "tracking", "street"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "motion_transfer"],
    },
    {
        "key": "video.action-sport.trail-pov",
        "name": "Trail point of view",
        "modality": "video",
        "category": "action-sport",
        "description": "A first-person flight or ride down a trail, for adventure openers.",
        "prompt_template": (
            "First-person point of view descending a {trail}, camera low and forward with quick "
            "natural banking around each turn, 14 mm equivalent, late afternoon sun cutting "
            "through the trees at intervals, the path rushing under the lens, motion blur at the "
            "frame edges only with the centre sharp, cool shadow tones with warm sun patches."
        ),
        "negative_template": "floating camera, path warping, trees bending, extreme fisheye, nausea roll, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 6, "fps": 30, "motion_strength": 0.85},
        "camera": {"move": "fpv", "speed": "fast"},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo of the trail for the terrain and tree cover."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.85, "duration_seconds": 6},
        "tags": ["sport", "fpv", "trail"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "motion_transfer"],
    },
    {
        "key": "video.dialogue-scene.over-shoulder-exchange",
        "name": "Over-shoulder exchange",
        "modality": "video",
        "category": "dialogue-scene",
        "description": "A held over-the-shoulder frame where the listener reacts to the speaker.",
        "prompt_template": (
            "Over-the-shoulder shot from behind {listener} toward {speaker} across a table, "
            "camera locked at seated eye height, 85 mm at f/2 so the near shoulder is a soft dark "
            "edge on the left, window light on the speaker's face from frame right, the "
            "listener's jaw and eyes moving slightly as they react, natural room tone colour, no "
            "cuts inside the shot."
        ),
        "negative_template": "lip flap out of sync, morphing face, extra shoulders, jump cuts, warped eyes, watermark",
        "aspect_ratio": "2.39:1",
        "model_preference": "",
        "parameters": {"duration_seconds": 6, "fps": 24, "motion_strength": 0.2},
        "camera": {"move": "over_the_shoulder", "speed": "slow"},
        "reference_requirements": [
            {"role": "character_identity", "required": False, "note": "Photos of both performers for consistent faces."},
            {"role": "environment", "required": False, "note": "A photo of the room for the window and table."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.2, "duration_seconds": 6},
        "tags": ["dialogue", "coverage", "reaction"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "lip_sync"],
    },
    {
        "key": "video.dialogue-scene.close-reaction",
        "name": "Close reaction hold",
        "modality": "video",
        "category": "dialogue-scene",
        "description": "A tight locked frame on one face as they take something in, for the cut after a line.",
        "prompt_template": (
            "Locked-off close-up of {character} from just below eye line, 100 mm at f/2 with only "
            "the eyes and mouth in critical focus, soft daylight from the left and a catchlight "
            "in both eyes, the head turning a few degrees as they listen and then settling, no "
            "camera movement, clean skin texture, restrained warm grade."
        ),
        "negative_template": "micro-expression flicker, rubber skin, drifting focus, eye colour changing, watermark",
        "aspect_ratio": "2.39:1",
        "model_preference": "",
        "parameters": {"duration_seconds": 5, "fps": 24, "motion_strength": 0.15},
        "camera": {"move": "static", "speed": "slow"},
        "reference_requirements": [
            {"role": "character_identity", "required": False, "note": "A photo of the performer for facial likeness."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.15, "duration_seconds": 5},
        "tags": ["dialogue", "close-up", "reaction"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "lip_sync"],
    },
    {
        "key": "video.landscape-drone.fjord-approach",
        "name": "Fjord approach",
        "modality": "video",
        "category": "landscape-drone",
        "description": "A low approach across water toward a cliff wall, for travel and title backgrounds.",
        "prompt_template": (
            "Low aerial approach across still water toward the {cliff} wall of a fjord, camera at "
            "around twenty metres and moving forward at a steady pace, 24 mm equivalent, early "
            "morning light with mist sitting at the waterline, the cliff filling the right two "
            "thirds of frame by the end, reflections unbroken, cool grade with warm light on the "
            "upper rock."
        ),
        "negative_template": "drone jello, spinning horizon, water mirror breaking, cliff morphing, noise, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 8, "fps": 30, "motion_strength": 0.5},
        "camera": {"move": "aerial", "speed": "slow"},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo of the fjord for the cliff profile."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.5, "duration_seconds": 8},
        "tags": ["drone", "landscape", "water"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "extend"],
    },
    {
        "key": "video.landscape-drone.desert-orbit",
        "name": "Desert orbit",
        "modality": "video",
        "category": "landscape-drone",
        "description": "A slow circular move around an isolated formation, for landscape reveals.",
        "prompt_template": (
            "Drone orbit around an isolated {formation} in a {desert} basin, camera circling at "
            "about forty metres with the formation held on the centre third, 35 mm equivalent, "
            "low sun from frame right so the shadow side turns through the shot, dune texture "
            "resolved, empty sky above, warm sand tones with cool shadow, steady speed."
        ),
        "negative_template": "orbit reversing, horizon tilt, formation changing shape, dune texture boiling, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 8, "fps": 30, "motion_strength": 0.45},
        "camera": {"move": "orbit", "speed": "slow"},
        "reference_requirements": [
            {"role": "environment", "required": False, "note": "A photo of the formation for its shape and setting."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.45, "duration_seconds": 8},
        "tags": ["drone", "desert", "orbit"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "extend"],
    },
    {
        "key": "video.food-film.steam-and-pour",
        "name": "Steam and pour",
        "modality": "video",
        "category": "food-film",
        "description": "A slow push toward a hot dish as sauce is poured, for menu films.",
        "prompt_template": (
            "Slow push toward a {dish} on a {colour} plate, sauce poured from a small jug at the "
            "top of frame and running down over the surface, camera moving in at a walking half "
            "pace, 90 mm at f/4, hard side light so the steam carries, the dark wooden table "
            "falling away at the edges, warm grade with honest highlights."
        ),
        "negative_template": "rubber sauce, steam rendering as smoke, camera overshooting, plate wobbling, text, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 5, "fps": 30, "motion_strength": 0.4},
        "camera": {"move": "push_in", "speed": "slow"},
        "reference_requirements": [
            {"role": "main_subject", "required": False, "note": "A photo of the plated dish for garnish and portion."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.4, "duration_seconds": 5},
        "tags": ["food", "steam", "push-in"],
        "modes": ["image_to_video", "text_to_video", "first_frame", "extend"],
    },
    {
        "key": "video.automotive-film.night-city-roll",
        "name": "Night city roll",
        "modality": "video",
        "category": "automotive-film",
        "description": "A tracking shot alongside a moving car through lit streets, for automotive films.",
        "prompt_template": (
            "Tracking shot running alongside {car} at road speed through a {city} street at "
            "night, camera mounted low at wheel height with the car held on the left third, "
            "16 mm at f/2.8, shop signage and streetlights streaking past behind, the wet road "
            "carrying reflections, the body highlights holding a clean line, teal and amber grade."
        ),
        "negative_template": "wheels not turning, car body warping, camera speed drift, doubled car, flickering lights, watermark",
        "aspect_ratio": "2.39:1",
        "model_preference": "",
        "parameters": {"duration_seconds": 6, "fps": 24, "motion_strength": 0.65},
        "camera": {"move": "tracking", "speed": "fast"},
        "reference_requirements": [
            {"role": "product", "required": True, "note": "A photo of the actual vehicle; the body shape must hold."},
            {"role": "environment", "required": False, "note": "A photo of the street for the signage and light mix."},
        ],
        "defaults": {"cfg": 4.5, "motion_strength": 0.65, "duration_seconds": 6},
        "tags": ["automotive", "night", "tracking"],
        "modes": ["image_to_video", "text_to_video", "first_frame", "motion_transfer"],
    },
    {
        "key": "video.character-animation.idle-loop",
        "name": "Character idle loop",
        "modality": "video",
        "category": "character-animation",
        "description": "A short breathing idle that loops cleanly, for game and app characters.",
        "prompt_template": (
            "Character animation of {character} standing in a neutral ready pose on a plain "
            "{colour} background, subtle idle motion: the chest rising with breath, weight "
            "shifting slightly between the feet, a slow blink every couple of seconds, camera "
            "locked, flat even lighting with no cast shadow, the first and last frames matching "
            "so the loop is seamless."
        ),
        "negative_template": "foot sliding, jittering limbs, changing costume, popping loop point, blur, watermark",
        "aspect_ratio": "1:1",
        "model_preference": "",
        "parameters": {"duration_seconds": 4, "fps": 24, "motion_strength": 0.25},
        "camera": {"move": "static", "speed": "slow"},
        "reference_requirements": [
            {"role": "character_identity", "required": True, "note": "The approved turnaround; the design must not drift."},
        ],
        "defaults": {"cfg": 4.0, "motion_strength": 0.25, "duration_seconds": 4},
        "tags": ["character", "loop", "idle"],
        "modes": ["image_to_video", "first_frame", "extend", "video_to_video"],
    },
    {
        "key": "video.character-animation.walk-cycle-stage",
        "name": "Walk cycle on a stage",
        "modality": "video",
        "category": "character-animation",
        "description": "A side-on walk cycle across a plain stage, for animation review and previews.",
        "prompt_template": (
            "Side-on walk cycle of {character} crossing a plain {colour} stage from left to "
            "right, camera tracking at the same speed so the character stays centred, the full "
            "body in frame with the feet on a marked line, flat even lighting with a soft contact "
            "shadow only, a four-step cadence with clear weight transfer, clean silhouette."
        ),
        "negative_template": "foot sliding, knee popping, limbs crossing the body, camera lag, changing height, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 4, "fps": 24, "motion_strength": 0.5},
        "camera": {"move": "tracking", "speed": "medium"},
        "reference_requirements": [
            {"role": "character_identity", "required": True, "note": "The approved design, so the costume and proportions hold."},
            {"role": "pose", "required": False, "note": "A key pose from the cycle you want to match."},
        ],
        "defaults": {"cfg": 4.0, "motion_strength": 0.5, "duration_seconds": 4},
        "tags": ["character", "walk-cycle", "animation"],
        "modes": ["image_to_video", "motion_transfer", "first_frame", "extend"],
    },
    {
        "key": "video.abstract-loop.fluid-orbit",
        "name": "Fluid orbit loop",
        "modality": "video",
        "category": "abstract-loop",
        "description": "A seamless abstract loop of fluid shapes drifting, for backgrounds and stings.",
        "prompt_template": (
            "Abstract three-dimensional form of {material} slowly morphing while the camera "
            "orbits a quarter turn, one soft key from above and a coloured rim from behind, the "
            "material reading as glossy but not mirror-like, plain dark background, motion "
            "constant in speed with no beat changes, first and last frames matching for a clean "
            "loop."
        ),
        "negative_template": "visible geometry seams, texture swimming, speed ramps, noisy shading, loop pop, watermark",
        "aspect_ratio": "1:1",
        "model_preference": "",
        "parameters": {"duration_seconds": 6, "fps": 30, "motion_strength": 0.4},
        "camera": {"move": "orbit", "speed": "slow"},
        "reference_requirements": [
            {"role": "color_palette", "required": False, "note": "A frame or swatch for the key and rim colours."},
        ],
        "defaults": {"cfg": 4.0, "motion_strength": 0.4, "duration_seconds": 6},
        "tags": ["abstract", "loop", "background"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "extend", "restyle"],
    },
    {
        "key": "video.title-sequence.type-assembly",
        "name": "Type assembly titles",
        "modality": "video",
        "category": "title-sequence",
        "description": "A title card where letterforms assemble and settle, for openers and end cards.",
        "prompt_template": (
            "Title sequence on a plain {colour} field where the words {title} assemble from flat "
            "letterforms sliding into place from off frame, camera pushing in slightly "
            "throughout, 50 mm equivalent, flat even lighting with no shadow, precise tracking "
            "and kerning held through the move, one accent line drawing under the final word, no "
            "other elements."
        ),
        "negative_template": "warped letterforms, flickering type, spelling drift, motion blur smear, extra text, watermark",
        "aspect_ratio": "16:9",
        "model_preference": "",
        "parameters": {"duration_seconds": 5, "fps": 25, "motion_strength": 0.35},
        "camera": {"move": "dolly_in", "speed": "slow"},
        "reference_requirements": [
            {"role": "style", "required": False, "note": "A brand frame showing the typeface and colour field."},
            {"role": "composition", "required": False, "note": "A storyboard for where the words land."},
        ],
        "defaults": {"cfg": 4.0, "motion_strength": 0.35, "duration_seconds": 5},
        "tags": ["titles", "type", "motion"],
        "modes": ["text_to_video", "image_to_video", "first_frame", "extend"],
    },
)


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------

#: Every built-in preset, image first. The order inside a modality is the order
#: the studio lists them, so related entries stay next to each other.
PRESETS: Tuple[dict, ...] = _IMAGE_PRESETS + _VIDEO_PRESETS

#: Key index. A preset key is stable and is the only identifier an API client
#: should store; the slug that encodes the category is for humans reading logs.
PRESETS_BY_KEY: Dict[str, dict] = {entry["key"]: entry for entry in PRESETS}


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def presets_for(modality: str) -> Tuple[dict, ...]:
    """Every preset for one modality, in catalogue order."""
    return tuple(entry for entry in PRESETS if entry["modality"] == modality)


def presets_in_category(category: str) -> Tuple[dict, ...]:
    """Every preset in one category, across both modalities."""
    return tuple(entry for entry in PRESETS if entry["category"] == category)


def preset(key: str) -> Optional[dict]:
    """The preset with this key, or None. An unknown key never raises."""
    return PRESETS_BY_KEY.get(key)


def all_category_labels(modality: str) -> List[dict]:
    """Categories for one modality with their label and current preset count."""
    if modality == "image":
        categories = IMAGE_CATEGORIES
    elif modality == "video":
        categories = VIDEO_CATEGORIES
    else:
        return []
    counts: Dict[str, int] = {}
    for entry in PRESETS:
        if entry["modality"] == modality:
            counts[entry["category"]] = counts.get(entry["category"], 0) + 1
    return [
        {
            "key": category,
            "label": CATEGORY_LABELS.get(category, category.replace("-", " ").capitalize()),
            "count": counts.get(category, 0),
        }
        for category in categories
    ]
