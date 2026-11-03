"""The canvas: a node graph over the generation routes.

What this is, and what it deliberately is not
---------------------------------------------
A canvas is a **document that says what to generate, in what order, and what
feeds what**. It is not a second execution engine. Running a canvas plans each
node into an ordinary generation request and queues it through
:class:`studio.services.generations.GenerationService`, so a node gets the same
capability checks, the same cost estimate, the same queue row, the same failure
reporting and the same asset import as a request typed into the studios.

That decision is what keeps the honesty properties intact. Nothing in this module
contacts a provider, invents a status or stores a picture. It validates a graph
and produces a plan; the queue does the rest.

The graph
---------
Two node kinds, because they are the two things the platform can actually do:

``generate``
    A prompt, a model, a mode, and optional parameter overrides. Exactly one
    generation job per run.

``note``
    A label on the canvas. It produces nothing and is never queued; it exists so a
    graph can say why a branch is there without a separate document.

An edge runs from an upstream node to a downstream node and carries a role, which
is what the downstream node does with the upstream output:

``input``
    The upstream output becomes the downstream request's source asset (a first
    frame, an image to edit, a clip to extend). This is the edge that makes a
    chain a chain.

``reference``
    The upstream output is attached as a reference with the role the edge names
    (``style``, ``character_identity`` and so on), for models that accept them.

Anything else is refused by name rather than ignored, because an edge a person
drew and the engine silently dropped is a graph that lies about what it does.

Cycles
------
A graph with a cycle cannot be ordered, and "run it in whatever order the JSON
lists" would queue a node before the frame it needs exists. The cycle is therefore
refused at save time and again at run time, naming the nodes involved.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .errors import VALIDATION_FAILED, ApiError

#: The node kinds a canvas may contain.
NODE_KINDS: Tuple[str, ...] = ("generate", "note")

#: What an edge does to the downstream node.
EDGE_ROLES: Tuple[str, ...] = ("input", "reference")

#: Reference roles an edge may name. Kept in step with studio.adapters.base.
EDGE_REFERENCE_ROLES: Tuple[str, ...] = (
    "main_subject",
    "character_identity",
    "composition",
    "pose",
    "lighting",
    "color_palette",
    "environment",
    "product",
    "style",
    "first_frame",
    "last_frame",
)

MAX_NODES = 60
MAX_EDGES = 180
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _error(message: str, field: str = "", **details: Any) -> ApiError:
    return ApiError(VALIDATION_FAILED, message, field=field, details=details or None)


# ---------------------------------------------------------------------------
# Node and edge validation
# ---------------------------------------------------------------------------


def normalise_node(raw: Mapping[str, Any], index: int) -> Dict[str, Any]:
    """One node, in the only shape the rest of this module reads.

    A node without an id is refused rather than given a generated one: the client
    draws edges by id, so an id the client did not choose is an edge to nowhere.
    """
    node_id = str(raw.get("id") or "").strip()
    if not node_id or not _ID.match(node_id):
        raise _error(
            "Each node needs an id of letters, digits or . _ : - (node %d)." % (index + 1),
            field="nodes",
        )
    kind = str(raw.get("kind") or "generate").strip().lower()
    if kind not in NODE_KINDS:
        raise _error(
            "Node '%s' has an unknown kind '%s'. A node is %s."
            % (node_id, kind, " or ".join("'%s'" % item for item in NODE_KINDS)),
            field="nodes",
        )
    node: Dict[str, Any] = {
        "id": node_id,
        "kind": kind,
        "label": str(raw.get("label") or "").strip()[:120],
        "position": _position(raw.get("position")),
    }
    if kind == "note":
        node["text"] = str(raw.get("text") or raw.get("label") or "").strip()[:4000]
        return node

    prompt = str(raw.get("prompt") or "").strip()
    if not prompt:
        raise _error(
            "Node '%s' has no prompt, so there is nothing to generate." % node_id,
            field="nodes",
        )
    if len(prompt) > 8000:
        raise _error("Node '%s' has a prompt longer than this platform accepts." % node_id, field="nodes")
    model_id = str(raw.get("model_id") or "").strip()
    if not model_id:
        raise _error(
            "Node '%s' has no model, so it cannot be planned." % node_id,
            field="nodes",
        )
    node.update(
        {
            "prompt": prompt,
            "negative_prompt": str(raw.get("negative_prompt") or "").strip()[:8000],
            "model_id": model_id,
            "mode": str(raw.get("mode") or "").strip(),
            "parameters": _parameters(raw.get("parameters")),
            "count": _count(raw.get("count")),
        }
    )
    return node


def _position(raw: Any) -> Dict[str, float]:
    if not isinstance(raw, Mapping):
        return {"x": 0.0, "y": 0.0}
    try:
        return {"x": round(float(raw.get("x") or 0.0), 2), "y": round(float(raw.get("y") or 0.0), 2)}
    except (TypeError, ValueError):
        raise _error("A node position must be a number pair.", field="nodes") from None


def _parameters(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise _error("A node's parameters must be an object.", field="nodes")
    return {str(name): value for name, value in raw.items()}


def _count(raw: Any) -> int:
    if raw is None or raw == "":
        return 1
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise _error("A node's output count must be a whole number.", field="nodes") from None
    if value < 1:
        raise _error("A node must ask for at least one output.", field="nodes")
    return value


def normalise_edge(raw: Mapping[str, Any], node_ids: Set[str]) -> Dict[str, Any]:
    """One edge, with both ends known and the role inside the vocabulary.

    An edge to a node that is not in the document is refused: it is the one class
    of mistake that would otherwise look like a working graph on screen and fail
    at run time with "no such node".
    """
    source = str(raw.get("from") or raw.get("source") or "").strip()
    target = str(raw.get("to") or raw.get("target") or "").strip()
    if not source or not target:
        raise _error("An edge needs a source and a target node.", field="edges")
    if source not in node_ids:
        raise _error("An edge starts at '%s', which is not a node here." % source, field="edges")
    if target not in node_ids:
        raise _error("An edge ends at '%s', which is not a node here." % target, field="edges")
    if source == target:
        raise _error("An edge cannot start and end at '%s'." % source, field="edges")
    role = str(raw.get("role") or "input").strip().lower()
    if role not in EDGE_ROLES:
        raise _error(
            "Edge %s -> %s has an unknown role '%s'. An edge is %s."
            % (source, target, role, " or ".join("'%s'" % item for item in EDGE_ROLES)),
            field="edges",
        )
    edge: Dict[str, Any] = {"from": source, "to": target, "role": role}
    if role == "reference":
        reference_role = str(raw.get("reference_role") or "style").strip().lower()
        if reference_role not in EDGE_REFERENCE_ROLES:
            raise _error(
                "Edge %s -> %s names an unknown reference role '%s'."
                % (source, target, reference_role),
                field="edges",
            )
        edge["reference_role"] = reference_role
    return edge


def normalise_graph(
    nodes: Any, edges: Any
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """The whole graph, normalised and proved acyclic.

    Refuses: a non-list; too many nodes or edges; a duplicate node id; a note with
    edges (a note produces nothing, so an edge from one is an edge from nothing);
    and any cycle, by naming the nodes on it rather than saying "invalid graph".
    """
    if not isinstance(nodes, list):
        raise _error("'nodes' must be a list.", field="nodes")
    if not isinstance(edges, list):
        raise _error("'edges' must be a list.", field="edges")
    if len(nodes) > MAX_NODES:
        raise _error("A canvas holds at most %d nodes." % MAX_NODES, field="nodes")
    if len(edges) > MAX_EDGES:
        raise _error("A canvas holds at most %d edges." % MAX_EDGES, field="edges")

    seen: Set[str] = set()
    clean_nodes: List[Dict[str, Any]] = []
    for index, raw in enumerate(nodes):
        if not isinstance(raw, Mapping):
            raise _error("Node %d is not an object." % (index + 1), field="nodes")
        node = normalise_node(raw, index)
        if node["id"] in seen:
            raise _error("Two nodes share the id '%s'." % node["id"], field="nodes")
        seen.add(node["id"])
        clean_nodes.append(node)

    clean_edges = [normalise_edge(raw, seen) for raw in edges if isinstance(raw, Mapping)]
    if len(clean_edges) != len(edges):
        raise _error("Every edge must be an object.", field="edges")

    by_id = {node["id"]: node for node in clean_nodes}
    for edge in clean_edges:
        for end in (edge["from"], edge["to"]):
            if by_id[end]["kind"] == "note":
                raise _error(
                    "Note '%s' cannot take part in an edge; a note produces nothing." % end,
                    field="edges",
                )
    order = topological_order(clean_nodes, clean_edges)
    return clean_nodes, clean_edges, order


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def topological_order(
    nodes: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]
) -> List[str]:
    """Node ids in an order where every node follows the nodes that feed it.

    Kahn's algorithm, with ties broken by the node's position on the canvas and
    then by id, so the same document always produces the same order. A cycle is
    reported with the ids still holding an unmet dependency -- the nodes actually
    on it -- rather than a generic "invalid graph".
    """
    ids = [str(node["id"]) for node in nodes]
    incoming: Dict[str, int] = {node_id: 0 for node_id in ids}
    outgoing: Dict[str, List[str]] = {node_id: [] for node_id in ids}
    for edge in edges:
        source = str(edge["from"])
        target = str(edge["to"])
        outgoing[source].append(target)
        incoming[target] = incoming.get(target, 0) + 1

    position = {str(node["id"]): node.get("position") or {} for node in nodes}

    def key(node_id: str) -> Tuple[float, float, str]:
        point = position.get(node_id) or {}
        # Reading order: the top row first, left to right, then the next row. The
        # previous sort was x-first, which interleaved rows on a grid; a graph is
        # read like a page, not like a spreadsheet column.
        return (float(point.get("y") or 0.0), float(point.get("x") or 0.0), node_id)

    ready = sorted([node_id for node_id, count in incoming.items() if count == 0], key=key)
    order: List[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for target in sorted(outgoing.get(current, []), key=key):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
        ready.sort(key=key)

    if len(order) != len(ids):
        stuck = sorted(set(ids) - set(order))
        raise _error(
            "These nodes feed each other in a loop, so no order exists: %s."
            % ", ".join("'%s'" % item for item in stuck),
            field="edges",
            nodes=stuck,
        )
    return order


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def plan_node(
    node: Mapping[str, Any],
    upstream: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """What one generate node needs from the queue, given what feeds it.

    The upstream edges become either a source asset or a set of references; the
    caller fills the asset id in from the job that produced it. Nothing is looked
    up here, so this stays a pure function a test can pin.
    """
    source_edge: Optional[Mapping[str, Any]] = None
    references: List[Dict[str, str]] = []
    for edge in upstream:
        if edge["role"] == "input":
            if source_edge is not None:
                raise _error(
                    "Node '%s' has two input edges, so there is no single source asset."
                    % node["id"],
                    field="edges",
                )
            source_edge = edge
        else:
            references.append(
                {"from": str(edge["from"]), "role": str(edge.get("reference_role") or "style")}
            )
    return {
        "node_id": str(node["id"]),
        "prompt": node["prompt"],
        "negative_prompt": node.get("negative_prompt") or "",
        "model_id": node["model_id"],
        "mode": node.get("mode") or "",
        "parameters": dict(node.get("parameters") or {}),
        "count": int(node.get("count") or 1),
        "source_from": str(source_edge["from"]) if source_edge else None,
        "references_from": references,
    }


def plan(
    nodes: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]
) -> List[Dict[str, Any]]:
    """Every generate node in execution order, with its upstream resolved.

    A node that produces nothing (a note) is absent from the plan: the plan is what
    the queue will be asked to do, not a copy of the document.
    """
    order = topological_order(nodes, edges)
    by_id = {str(node["id"]): node for node in nodes}
    upstream_by_target: Dict[str, List[Mapping[str, Any]]] = {}
    for edge in edges:
        upstream_by_target.setdefault(str(edge["to"]), []).append(edge)
    steps: List[Dict[str, Any]] = []
    for node_id in order:
        node = by_id[node_id]
        if node["kind"] != "generate":
            continue
        steps.append(plan_node(node, upstream_by_target.get(node_id, [])))
    return steps


def describe(nodes: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """A summary the canvas screen can render without re-deriving anything.

    Counts and the order only. It is intentionally not a second copy of the graph:
    a client that shows the order from here and the nodes from the document cannot
    disagree with what a run will do.
    """
    order = topological_order(nodes, edges)
    by_id = {str(node["id"]): node for node in nodes}
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "generate_count": sum(1 for node in nodes if node["kind"] == "generate"),
        "note_count": sum(1 for node in nodes if node["kind"] == "note"),
        "order": order,
        "stages": [
            {
                "node_id": node_id,
                "label": by_id[node_id].get("label") or "",
                "kind": by_id[node_id]["kind"],
            }
            for node_id in order
        ],
    }
