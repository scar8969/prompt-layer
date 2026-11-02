"""Marketing Studio: a product page in, a campaign of real generations out.

  GET  /api/marketing/formats    the formats and their aspect ratios
  POST /api/marketing/brief      read a URL (or accept pasted facts) and compose a brief
  POST /api/marketing/campaigns  queue one real generation per chosen format

Two halves, kept visibly separate. The brief route is **deterministic text work**:
it fetches the page through the SSRF-guarded egress client, reads the metadata the
page states, and composes copy from that plus what the author typed. It calls no
model. The campaign route **queues ordinary generation jobs** on the user's own
key, one per format, and returns their ids immediately.

That split is what makes the copy checkable. Every field in a brief names its
source, and a field the page did not state stays empty with a warning rather than
being filled with something plausible.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

from .. import marketing
from ..errors import VALIDATION_FAILED, ApiError
from ..http import RequestContext, Response
from ..logging_setup import get_logger

LOG = get_logger(__name__)

#: A page fetch is not a generation: it must answer quickly or not at all.
FETCH_TIMEOUT_SECONDS = 12.0


def _facts_from_body(body: Mapping[str, Any]) -> Dict[str, Any]:
    """Page facts the author pasted, used verbatim.

    A caller that already has the facts should not have to expose a URL just to
    have them read back. This path exists for a page behind a login, or for a
    product the author is describing from memory -- and it is marked in the brief
    as pasted rather than fetched, so nobody mistakes it for a page read.
    """
    title = str(body.get("title") or "").strip()
    description = str(body.get("description") or "").strip()
    if not title and not description:
        raise ApiError(
            VALIDATION_FAILED,
            "Paste a title or a description, or give a URL to read.",
            field="title",
        )
    return {
        "url": str(body.get("url") or ""),
        "title": title[: marketing.MAX_TITLE],
        "description": description[: marketing.MAX_DESCRIPTION],
        "site_name": str(body.get("site_name") or "").strip()[:120],
        "image": str(body.get("image") or "").strip()[:2000],
        "features": [str(item)[:160] for item in (body.get("features") or [])][: marketing.MAX_FEATURES],
        "sources": {"title": "pasted", "description": "pasted"},
        "missing": [name for name in ("title", "description") if not (title if name == "title" else description)],
    }


def register_routes(router: Any, app: Any) -> None:
    """Attach this group to the router. Called once, at startup."""

    def formats(request: RequestContext) -> Response:
        """The formats a campaign can render, and what each one is for."""
        return Response.json(
            {
                "formats": [
                    {
                        "key": key,
                        "label": marketing.FORMAT_LABELS[key],
                        "aspect_ratio": marketing.FORMAT_ASPECTS[key],
                    }
                    for key in marketing.FORMATS
                ],
                "tones": list(marketing.TONES),
            }
        )

    def brief(request: RequestContext) -> Response:
        """Compose a campaign brief, from a page or from pasted facts.

        Refuses a URL that the egress policy will not fetch, and a page that
        states nothing usable -- in both cases with the reason, because a brief
        built from an empty page would produce an ad about nothing.
        """
        body = request.json()
        url = str(body.get("url") or "").strip()
        if url:
            from ..net import client_for

            client = client_for(app.config)
            try:
                response = client.request(
                    "GET",
                    url,
                    accept="text/html,application/xhtml+xml",
                    max_bytes=marketing.MAX_PAGE_BYTES,
                    timeout_seconds=FETCH_TIMEOUT_SECONDS,
                    read_timeout=FETCH_TIMEOUT_SECONDS,
                )
            except ApiError:
                raise
            except Exception as error:  # noqa: BLE001 - a fetch failure is a 400, not a 500
                raise ApiError(
                    VALIDATION_FAILED,
                    "That page could not be read.",
                    field="url",
                    details={"reason": type(error).__name__},
                    hint="Check the address, or paste the product details instead.",
                ) from None
            if not response.ok:
                raise ApiError(
                    VALIDATION_FAILED,
                    "That page answered %d, so there is nothing to read." % response.status,
                    field="url",
                    details={"status": response.status},
                    hint="Check the address, or paste the product details instead.",
                )
            facts = marketing.parse_product_page(response.text(), response.url)
            if not facts.get("title") and not facts.get("description"):
                raise ApiError(
                    VALIDATION_FAILED,
                    "That page states no title and no description, so a brief built from it would invent one.",
                    field="url",
                    hint="Paste the product details instead.",
                )
            facts["sources"]["page"] = response.url
        else:
            facts = _facts_from_body(body)

        document = marketing.build_brief(
            facts,
            audience=str(body.get("audience") or ""),
            benefit=str(body.get("benefit") or ""),
            tone=str(body.get("tone") or "direct"),
        )
        return Response.json(
            {
                "brief": document,
                "facts": {key: value for key, value in facts.items() if key != "sources"} | {
                    "sources": facts.get("sources") or {}
                },
            }
        )

    def campaign(request: RequestContext) -> Response:
        """Queue one real generation per format.

        The brief is rebuilt from the same facts the caller sent, so a campaign
        cannot render a format whose prompt was not the one the response shows.
        Every job is an ordinary generation on the chosen model; nothing here
        contacts a provider.
        """
        request.session.require("generation:create")
        body = request.json()
        model_id = str(body.get("model_id") or "").strip()
        if not model_id:
            raise ApiError(
                VALIDATION_FAILED,
                "Choose the model that should render the campaign.",
                field="model_id",
                hint="A campaign uses your own key, so it needs a connected model.",
            )
        formats = marketing.validate_formats(body.get("formats"))
        url = str(body.get("url") or "").strip()
        if url:
            from ..net import client_for

            client = client_for(app.config)
            response = client.request(
                "GET",
                url,
                accept="text/html,application/xhtml+xml",
                max_bytes=marketing.MAX_PAGE_BYTES,
                timeout_seconds=FETCH_TIMEOUT_SECONDS,
                read_timeout=FETCH_TIMEOUT_SECONDS,
            )
            if not response.ok:
                raise ApiError(
                    VALIDATION_FAILED,
                    "That page answered %d, so there is nothing to render from." % response.status,
                    field="url",
                )
            facts = marketing.parse_product_page(response.text(), response.url)
        else:
            facts = _facts_from_body(body)
        document = marketing.build_brief(
            facts,
            audience=str(body.get("audience") or ""),
            benefit=str(body.get("benefit") or ""),
            tone=str(body.get("tone") or "direct"),
        )

        model = app.repos.models.get(request.session.workspace_id, model_id)
        modality = str(model.get("modality") or "image")
        if modality != "image":
            raise ApiError(
                VALIDATION_FAILED,
                "A campaign renders stills, so it needs an image model.",
                field="model_id",
            )
        declared = [str(item) for item in (model.get("modes") or [])]
        if declared and "text_to_image" not in declared:
            raise ApiError(
                "capability_unsupported",
                "The chosen model does not declare text to image.",
                field="model_id",
                details={"supported": declared},
            )
        project_id = str(body.get("project_id") or "").strip() or None
        if project_id:
            app.repos.projects.get(request.session.workspace_id, project_id)

        queued: List[Dict[str, Any]] = []
        by_key = {item["key"]: item for item in document["formats"]}
        for key in formats:
            plan = by_key[key]
            payload: Dict[str, Any] = {
                "model_id": model_id,
                "mode": "text_to_image",
                "prompt": plan["prompt"],
                "count": 1,
                "aspect_ratio": plan["aspect_ratio"],
                "project_id": project_id,
            }
            result = app.generations.queue(
                request.session, payload, modality="image", mode="text_to_image"
            )
            job = result.get("job") or {}
            queued.append(
                {
                    "format": key,
                    "label": plan["label"],
                    "aspect_ratio": plan["aspect_ratio"],
                    "headline": plan["headline"],
                    "caption": plan["caption"],
                    "prompt": plan["prompt"],
                    "job_id": str(job.get("id") or ""),
                }
            )
        return Response.json(
            {
                "campaign": {
                    "brief": document,
                    "model_id": model_id,
                    "project_id": project_id,
                    "renders": queued,
                }
            },
            status=202,
        )

    router.add("GET", "/api/marketing/formats", formats)
    router.add("POST", "/api/marketing/brief", brief)
    router.add("POST", "/api/marketing/campaigns", campaign)
