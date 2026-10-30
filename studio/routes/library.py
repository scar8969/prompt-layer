"""The creative library: presets, saved prompts, characters, and usage records."""

from __future__ import annotations

import base64
import binascii
from typing import Any, Dict, Mapping

from ..errors import VALIDATION_FAILED, ApiError
from ..http import RequestContext, Response
from ..services import assistant as assistant_service
from . import _shape


def _decode(value: Any) -> bytes:
    """Decode a base64 payload, refusing malformed data by name."""
    text = str(value or "")
    if not text:
        raise ApiError(VALIDATION_FAILED, "The image is empty.", field="data_base64")
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(
            VALIDATION_FAILED, "The image is not valid base64.", field="data_base64"
        ) from None


def _builtin_preset(entry: Mapping[str, Any]) -> Dict[str, Any]:
    """One catalogue preset, in exactly the shape a saved preset arrives in.

    Two vocabularies had to be reconciled, and only one direction is honest:
    the *stored* row is what the library table can return, so a catalogue entry is
    translated into that shape rather than the other way round.

    | catalogue (`presets_data.py`) | row (`presets` table) |
    |---|---|
    | `key`                         | `id` = `builtin:<key>`, and `slug` = `<key>` |
    | `modality`                    | `kind`                |
    | `negative_template`           | `negative_prompt_template` |
    | `parameters`                  | `defaults`            |
    | `camera`                      | `camera_settings`     |
    | `reference_requirements`      | `required_references` |

    The dataset's `aspect_ratio` is a property of the look rather than a model
    parameter, so it travels inside `defaults` under its own name and the studio
    maps it to the frame control instead of sending it as a parameter.

    A catalogue entry is a document, not a row: it has no workspace and no
    creation time, and the client is told so rather than being handed a
    plausible-looking timestamp.
    """
    defaults = dict(entry.get("parameters") or {})
    if entry.get("aspect_ratio"):
        defaults["aspect_ratio"] = entry["aspect_ratio"]
    return {
        "id": "builtin:%s" % entry["key"],
        "slug": entry["key"],
        "kind": entry["modality"],
        "name": entry["name"],
        "description": entry.get("description") or "",
        "category": entry.get("category") or "general",
        "thumbnail_url": None,
        "prompt_template": entry.get("prompt_template") or "",
        "negative_prompt_template": entry.get("negative_template") or "",
        "defaults": defaults,
        "supported_modes": list(entry.get("modes") or []),
        "required_references": list(entry.get("reference_requirements") or []),
        "camera_settings": dict(entry.get("camera") or {}),
        "tags": list(entry.get("tags") or []),
        "notes": entry.get("notes") or "",
        "is_builtin": True,
        "workspace_id": None,
        "created_by_user_id": None,
        "created_at": None,
        "updated_at": None,
    }


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    # -- presets -----------------------------------------------------------

    def presets(request: RequestContext) -> Response:
        """The catalogue for one modality, plus whatever this workspace saved.

        The built-in catalogue and the workspace library are returned together,
        each marked, so the client can show one grid without guessing which is
        editable.
        """
        from ..presets_data import all_category_labels, presets_for

        modality = request.arg("modality") or "image"
        builtin = [_builtin_preset(entry) for entry in presets_for(modality)]
        favorite_only = request.flag("favorite")
        saved = app.repos.library.list_presets(
            request.session.workspace_id,
            kind=modality,
            category=request.arg("category") or None,
            favorite=favorite_only,
            # Favourites are per person, so the filter needs to know whose.
            user_id=request.session.user_id if favorite_only else None,
            include_builtin=False,
        )
        return Response.json(
            {
                "presets": list(builtin) + list(saved),
                "builtin_count": len(builtin),
                "saved_count": len(saved),
                "categories": all_category_labels(modality),
            }
        )

    def create_preset(request: RequestContext) -> Response:
        """Save a preset to this workspace library."""
        request.session.require("preset:manage")
        preset = app.repos.library.create_preset(
            request.session.workspace_id, request.session.user_id, request.json()
        )
        return Response.json({"preset": _shape.row(preset)}, status=201)

    def show_preset(request: RequestContext) -> Response:
        """One saved preset, or a built-in one by its key."""
        from ..presets_data import preset as builtin_preset

        identifier = request.params["id"]
        if identifier.startswith("builtin:"):
            entry = builtin_preset(identifier.split(":", 1)[1])
            found = _builtin_preset(entry) if entry else None
        else:
            found = app.repos.library.get_preset(request.session.workspace_id, identifier)
        return Response.json({"preset": found})

    def update_preset(request: RequestContext) -> Response:
        """Change a saved preset."""
        request.session.require("preset:manage")
        preset = app.repos.library.update_preset(
            request.session.workspace_id, request.params["id"], request.json()
        )
        return Response.json({"preset": _shape.row(preset)})

    def delete_preset(request: RequestContext) -> Response:
        """Remove a saved preset."""
        request.session.require("preset:manage")
        app.repos.library.soft_delete_preset(request.session.workspace_id, request.params["id"])
        return Response.json({"ok": True})

    def favorite_preset(request: RequestContext) -> Response:
        """Mark or unmark a saved preset as a favourite."""
        body = request.json()
        preset = app.repos.library.set_preset_favorite(
            request.session.workspace_id,
            request.params["id"],
            bool(body.get("favorite", True)),
        )
        return Response.json({"preset": _shape.row(preset)})

    # -- saved prompts -----------------------------------------------------

    def prompts(request: RequestContext) -> Response:
        """The workspace saved prompts, most recently used first."""
        rows = app.repos.library.list_prompts(
            request.session.workspace_id, q=request.arg("q") or None
        )
        return Response.json({"prompts": _shape.rows(rows)})

    def create_prompt(request: RequestContext) -> Response:
        """Save a prompt."""
        prompt = app.repos.library.create_prompt(
            request.session.workspace_id, request.session.user_id, request.json()
        )
        return Response.json({"prompt": _shape.row(prompt)}, status=201)

    def show_prompt(request: RequestContext) -> Response:
        """One saved prompt."""
        prompt = app.repos.library.get_prompt(request.session.workspace_id, request.params["id"])
        return Response.json({"prompt": _shape.row(prompt)})

    def update_prompt(request: RequestContext) -> Response:
        """Change a saved prompt."""
        prompt = app.repos.library.update_prompt(
            request.session.workspace_id, request.params["id"], request.json()
        )
        return Response.json({"prompt": _shape.row(prompt)})

    def use_prompt(request: RequestContext) -> Response:
        """Record that a saved prompt was used, for the recently-used order."""
        prompt = app.repos.library.mark_prompt_used(request.session.workspace_id, request.params["id"])
        return Response.json({"prompt": _shape.row(prompt)})

    def delete_prompt(request: RequestContext) -> Response:
        """Remove a saved prompt."""
        app.repos.library.soft_delete_prompt(request.session.workspace_id, request.params["id"])
        return Response.json({"ok": True})

    # -- characters --------------------------------------------------------

    def characters(request: RequestContext) -> Response:
        """The character library for this workspace."""
        rows = app.repos.library.list_characters(
            request.session.workspace_id, q=request.arg("q") or None
        )
        return Response.json({"characters": _shape.rows(rows)})

    def create_character(request: RequestContext) -> Response:
        """Create a character with its visual description."""
        request.session.require("character:manage")
        character = app.repos.library.create_character(
            request.session.workspace_id, request.session.user_id, request.json()
        )
        out = _shape.row(character)
        out["reference_count"] = len(out.get("references") or [])
        return Response.json({"character": out}, status=201)

    def show_character(request: RequestContext) -> Response:
        """One character with its references, and the count the list also reports.

        The list projection carries `reference_count` because it does not carry the
        rows; without the same field here, a client that reads one shape has to
        branch on which route it called.
        """
        character = app.repos.library.get_character(
            request.session.workspace_id, request.params["id"]
        )
        out = _shape.row(character)
        out["reference_count"] = len(out.get("references") or [])
        return Response.json({"character": out})

    def update_character(request: RequestContext) -> Response:
        """Change a character."""
        request.session.require("character:manage")
        character = app.repos.library.update_character(
            request.session.workspace_id, request.params["id"], request.json()
        )
        return Response.json({"character": _shape.row(character)})

    def delete_character(request: RequestContext) -> Response:
        """Remove a character. Its assets stay in the library."""
        request.session.require("character:manage")
        app.repos.library.soft_delete_character(request.session.workspace_id, request.params["id"])
        return Response.json({"ok": True})

    def add_reference(request: RequestContext) -> Response:
        """Attach a reference image to a character.

        The image is stored through the same inspection as any other upload, so a
        character reference cannot smuggle in a file type the platform refuses.
        """
        request.session.require("character:manage")
        body = request.json()
        character_id = request.params["id"]
        app.repos.library.get_character(request.session.workspace_id, character_id)
        asset = None
        asset_id = str(body.get("asset_id") or "").strip()
        if asset_id:
            app.repos.assets.get(request.session.workspace_id, asset_id)
        else:
            asset = app.assets.upload(
                request.session,
                data=_decode(body.get("data_base64")),
                filename=str(body.get("file_name") or "character-reference"),
                declared_type=str(body.get("content_type") or ""),
            )
            asset_id = str(asset.get("id") or "")
        reference = app.repos.library.add_character_reference(
            request.session.workspace_id,
            character_id,
            {
                "asset_id": asset_id,
                "role": str(body.get("role") or "identity"),
                "notes": str(body.get("notes") or ""),
            },
        )
        return Response.json({"reference": reference}, status=201)

    def remove_reference(request: RequestContext) -> Response:
        """Detach a reference image from a character."""
        request.session.require("character:manage")
        app.repos.library.remove_character_reference(
            request.session.workspace_id, request.params["id"], request.params["referenceId"]
        )
        return Response.json({"ok": True})

    # -- usage -------------------------------------------------------------

    def usage(request: RequestContext) -> Response:
        """What this workspace has generated, with no invented amounts.

        Costs appear only when a provider reported one; the summary says how many
        rows carry an amount so a zero is never mistaken for free.
        """
        limit = request.arg("limit") or "50"
        try:
            size = max(1, min(int(limit), 200))
        except ValueError:
            raise ApiError(VALIDATION_FAILED, "The limit must be a whole number.", field="limit") from None
        rows = app.repos.usage.list(request.session.workspace_id, limit=size)
        return Response.json(
            {
                "usage": _shape.rows(rows),
                "summary": app.repos.usage.summary(request.session.workspace_id),
            }
        )

    # -- prompt assistant --------------------------------------------------

    def assistant(request: RequestContext) -> Response:
        """Improve or restructure a prompt without sending it anywhere.

        The response is a suggestion plus a list of exactly what changed, so the
        client can show a before/after and let the author accept it. Nothing here
        calls a model: no prompt leaves this server.
        """
        body = request.json()
        prompt = str(body.get("prompt") or "")
        return Response.json(
            {
                "improvement": assistant_service.improve(
                    prompt,
                    operation=str(body.get("operation") or "structure"),
                    negative_prompt=str(body.get("negative_prompt") or ""),
                )
            }
        )

    def assistant_capabilities(request: RequestContext) -> Response:
        """What the assistant does, and what it refuses to claim."""
        return Response.json({"assistant": assistant_service.describe_capabilities()})

    router.add("GET", "/api/presets", presets)
    router.add("POST", "/api/presets", create_preset)
    router.add("GET", "/api/presets/{id}", show_preset)
    router.add("PATCH", "/api/presets/{id}", update_preset)
    router.add("DELETE", "/api/presets/{id}", delete_preset)
    router.add("POST", "/api/presets/{id}/favorite", favorite_preset)
    router.add("GET", "/api/prompts", prompts)
    router.add("POST", "/api/prompts", create_prompt)
    router.add("GET", "/api/prompts/{id}", show_prompt)
    router.add("PATCH", "/api/prompts/{id}", update_prompt)
    router.add("POST", "/api/prompts/{id}/use", use_prompt)
    router.add("DELETE", "/api/prompts/{id}", delete_prompt)
    router.add("GET", "/api/characters", characters)
    router.add("POST", "/api/characters", create_character)
    router.add("GET", "/api/characters/{id}", show_character)
    router.add("PATCH", "/api/characters/{id}", update_character)
    router.add("DELETE", "/api/characters/{id}", delete_character)
    router.add("POST", "/api/characters/{id}/references", add_reference)
    router.add("DELETE", "/api/characters/{id}/references/{referenceId}", remove_reference)
    router.add("GET", "/api/usage", usage)
    router.add("POST", "/api/prompts/improve", assistant)
    # Deliberately not GET /api/prompts/improve: that path is also matched by
    # GET /api/prompts/{id}, and the earlier registration would win, silently
    # answering with a prompt lookup. A distinct path cannot shadow anything.
    router.add("GET", "/api/assistant/capabilities", assistant_capabilities)
