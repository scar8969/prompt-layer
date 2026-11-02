import { useEffect, useMemo, useRef, useState } from 'react';
import { canvases as canvasesApi, models as modelsApi } from '../../api/endpoints.ts';
import type { Canvas, CanvasEdge, CanvasNode, CanvasRun, Model } from '../../api/types.ts';
import { Button } from '../../components/Button.tsx';
import { Dialog } from '../../components/Dialog.tsx';
import { TextArea, TextInput } from '../../components/Field.tsx';import { Icon } from '../../components/Icon.tsx';
import { Badge } from '../../components/Badge.tsx';
import { useQuery, invalidate } from '../../state/query.ts';
import { useToasts } from '../../state/toasts.tsx';
import { ModelPicker } from '../studio/ModelPicker.tsx';

/**
 * The canvas.
 *
 * A node graph over the generation routes, not a second engine. Every node maps
 * to one ordinary generation job, so the run screen can hand a node's job id to
 * the same History screen as any other request. The board deliberately shows the
 * execution order the server computed rather than the order nodes were drawn:
 * that is the order the run will use.
 */

let sequence = 0;

function newNode(kind: CanvasNode['kind'], index: number, modelId = ''): CanvasNode {
  sequence += 1;
  const id = `${kind}-${Date.now().toString(36)}-${sequence}`;
  return {
    id,
    kind,
    label: kind === 'note' ? 'Note' : `Step ${sequence}`,
    position: { x: 40 + (index % 4) * 240, y: 40 + Math.floor(index / 4) * 180 },
    ...(kind === 'note'
      ? { text: '' }
      : { prompt: '', negative_prompt: '', model_id: modelId, mode: '', count: 1 }),
  };
}

export function CanvasScreen() {
  const toasts = useToasts();
  const [selectedId, setSelectedId] = useState('');
  const [draft, setDraft] = useState<Canvas | null>(null);
  const [saving, setSaving] = useState(false);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState('');
  const [run, setRun] = useState<CanvasRun | null>(null);
  const [creating, setCreating] = useState(false);
  const boardRef = useRef<HTMLDivElement | null>(null);

  const list = useQuery<Canvas[]>('canvases', () => canvasesApi.list(), { staleMs: 10_000 });
  const models = useQuery<Model[]>('models?enabled=1', () => modelsApi.list({ enabled: true }), {
    staleMs: 30_000,
  });
  const eligibleModels = models.data ?? [];

  // Selecting from the list loads a working copy. Edits are local until Save,
  // which is what makes Cancel possible and what stops a stray drag from writing.
  useEffect(() => {
    if (!selectedId) {
      setDraft(null);
      return;
    }
    const found = (list.data ?? []).find((item) => item.id === selectedId);
    if (found) setDraft(structuredClone(found));
  }, [selectedId, list.data]);

  const update = (patch: Partial<Canvas>) => setDraft((current) => (current ? { ...current, ...patch } : current));

  const addNode = (kind: CanvasNode['kind']) => {
    setDraft((current) => {
      const base = current ?? {
        id: '',
        workspace_id: '',
        created_by_user_id: '',
        name: 'Untitled canvas',
        description: '',
        nodes: [],
        edges: [],
        created_at: '',
        updated_at: '',
      };
      const node = newNode(kind, base.nodes.length, eligibleModels[0]?.id ?? '');
      setSelectedNode(node.id);
      return { ...base, nodes: [...base.nodes, node] };
    });
  };

  const removeNode = (nodeId: string) => {
    setDraft((current) =>
      current
        ? {
            ...current,
            nodes: current.nodes.filter((node) => node.id !== nodeId),
            edges: current.edges.filter((edge) => edge.from !== nodeId && edge.to !== nodeId),
          }
        : current,
    );
    if (selectedNode === nodeId) setSelectedNode('');
  };

  const patchNode = (nodeId: string, fields: Partial<CanvasNode>) => {
    setDraft((current) =>
      current
        ? { ...current, nodes: current.nodes.map((node) => (node.id === nodeId ? { ...node, ...fields } : node)) }
        : current,
    );
  };

  const connect = (target: string) => {
    if (!connecting || connecting === target) {
      setConnecting(null);
      return;
    }
    setDraft((current) => {
      if (!current) return current;
      if (current.edges.some((edge) => edge.from === connecting && edge.to === target)) {
        toasts.push({ tone: 'info', title: 'Already connected', text: 'Those two nodes already pass an output.' });
        return current;
      }
      const edge: CanvasEdge = { from: connecting, to: target, role: 'input' };
      return { ...current, edges: [...current.edges, edge] };
    });
    setConnecting(null);
  };

  const removeEdge = (index: number) => {
    setDraft((current) =>
      current ? { ...current, edges: current.edges.filter((_, position) => position !== index) } : current,
    );
  };

  const startDrag = (event: React.PointerEvent, node: CanvasNode) => {
    if (event.button !== 0) return;
    const board = boardRef.current;
    if (!board) return;
    event.preventDefault();
    const origin = board.getBoundingClientRect();
    const offsetX = event.clientX - origin.left - node.position.x;
    const offsetY = event.clientY - origin.top - node.position.y;
    const move = (moveEvent: PointerEvent) => {
      patchNode(node.id, {
        position: {
          x: Math.max(0, Math.round(moveEvent.clientX - origin.left - offsetX)),
          y: Math.max(0, Math.round(moveEvent.clientY - origin.top - offsetY)),
        },
      });
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  const save = async (): Promise<Canvas | null> => {
    if (!draft) return null;
    setSaving(true);
    try {
      const saved = draft.id
        ? await canvasesApi.patch(draft.id, {
            name: draft.name,
            description: draft.description,
            nodes: draft.nodes,
            edges: draft.edges,
          })
        : await canvasesApi.create({
            name: draft.name || 'Untitled canvas',
            description: draft.description,
            nodes: draft.nodes,
            edges: draft.edges,
          });
      invalidate('canvases');
      list.reload();
      setSelectedId(saved.id);
      setDraft(saved);
      toasts.push({ tone: 'success', title: 'Canvas saved', text: `${saved.nodes.length} node(s).` });
      return saved;
    } catch (error) {
      toasts.push({
        tone: 'error',
        title: 'The canvas was refused',
        text: error instanceof Error ? error.message : 'The graph could not be saved.',
      });
      return null;
    } finally {
      setSaving(false);
    }
  };

  const runCanvas = async () => {
    if (!draft) return;
    const saved = draft.id && draft.nodes.length ? draft : await save();
    if (!saved) return;
    try {
      const started = await canvasesApi.startRun(saved.id);
      setRun(started);
      toasts.push({ tone: 'info', title: 'Canvas started', text: 'Each node queues a real generation job.' });
    } catch (error) {
      toasts.push({
        tone: 'error',
        title: 'The run was refused',
        text: error instanceof Error ? error.message : 'The canvas could not start.',
      });
    }
  };

  // Poll the run while it moves. The read itself advances the run: the server
  // uses the same "a poll is the clock" rule the job screen relies on.
  useEffect(() => {
    if (!run || !draft?.id) return;
    if (['completed', 'failed', 'cancelled'].includes(run.status)) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const next = await canvasesApi.getRun(draft.id, run.id);
        if (!cancelled) setRun(next);
      } catch {
        /* the next poll will report the real failure */
      }
    }, 1200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [run, draft?.id]);

  const ordered = useMemo(() => {
    if (!draft) return [] as CanvasNode[];
    const order = draft.summary?.order ?? draft.nodes.map((node) => node.id);
    return order
      .map((id) => draft.nodes.find((node) => node.id === id))
      .filter((node): node is CanvasNode => Boolean(node));
  }, [draft]);

  const nodeById = (id: string) => draft?.nodes.find((node) => node.id === id) ?? null;
  const active = selectedNode ? nodeById(selectedNode) : null;

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__heading">Canvas</h1>
          <p className="page__subtitle">
            A DAG of real generation jobs. Each node is one request on your own key; a node is queued only
            after the nodes that feed it have a stored output.
          </p>
        </div>
        <div className="row row--tight">
          <Button onClick={() => setCreating(true)}>
            <span className="btn__icon">
              <Icon name="plus" size={13} />
            </span>
            New canvas
          </Button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr', gap: 'var(--s4)', alignItems: 'start' }}>
        <aside className="panel" style={{ padding: 'var(--s3)' }}>
          <p className="panel__title" style={{ marginBottom: 8 }}>
            Canvases
          </p>
          {list.loading ? <p className="dim">Reading…</p> : null}
          {!list.loading && !(list.data ?? []).length ? (
            <p className="dim" style={{ margin: 0 }}>
              No canvas yet. Create one and add a generate node.
            </p>
          ) : null}
          <div className="stack stack--tight">
            {(list.data ?? []).map((item) => (
              <button
                key={item.id}
                type="button"
                className={`btn btn--sm${item.id === selectedId ? ' btn--primary' : ''}`}
                style={{ justifyContent: 'flex-start', textAlign: 'left' }}
                onClick={() => {
                  setSelectedId(item.id);
                  setRun(null);
                  setSelectedNode('');
                }}
              >
                {item.name}
                <span className="dim" style={{ marginLeft: 'auto', fontSize: 11 }}>
                  {item.summary?.generate_count ?? item.nodes.length}
                </span>
              </button>
            ))}
          </div>
        </aside>

        <section className="stack">
          {!draft ? (
            <div className="panel" style={{ minHeight: 320, display: 'grid', placeItems: 'center' }}>
              <p className="dim" style={{ margin: 0 }}>
                Select a canvas, or create one.
              </p>
            </div>
          ) : (
            <>
              <div className="panel" style={{ padding: 'var(--s3)' }}>
                <div className="row row--tight" style={{ flexWrap: 'wrap', gap: 8, alignItems: 'end' }}>
                  <div style={{ flex: 1, minWidth: 220 }}>
                    <TextInput
                      label="Name"
                      value={draft.name}
                      onChange={(event) => update({ name: event.target.value })}
                    />
                  </div>
                  <Button onClick={() => addNode('generate')}>
                    <span className="btn__icon">
                      <Icon name="plus" size={13} />
                    </span>
                    Generate node
                  </Button>
                  <Button onClick={() => addNode('note')}>Note</Button>
                  <Button onClick={() => void save()} disabled={saving}>
                    {saving ? 'Saving…' : 'Save'}
                  </Button>
                  <Button variant="primary" onClick={() => void runCanvas()} disabled={!draft.nodes.some((n) => n.kind === 'generate')}>
                    <span className="btn__icon">
                      <Icon name="play" size={13} />
                    </span>
                    Run canvas
                  </Button>
                </div>

                <div className="row row--tight" style={{ marginTop: 10, flexWrap: 'wrap', gap: 8 }}>
                  <p className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
                    Execution order:{' '}
                    {ordered.length
                      ? ordered.map((node) => node.label || node.id).join(' → ')
                      : 'nothing to run yet'}
                  </p>
                  <span className="topbar__spacer" />
                  {connecting ? (
                    <Badge tone="accent">
                      Connecting from {nodeById(connecting)?.label || connecting} — click the target
                    </Badge>
                  ) : null}
                  {run ? <Badge tone={run.status === 'failed' ? 'danger' : run.status === 'completed' ? 'ok' : 'accent'}>{run.status}</Badge> : null}
                </div>
              </div>

              <div
                ref={boardRef}
                className="panel"
                style={{
                  position: 'relative',
                  minHeight: 520,
                  overflow: 'auto',
                  background:
                    'linear-gradient(var(--line) 1px, transparent 1px) 0 0 / 28px 28px, linear-gradient(90deg, var(--line) 1px, transparent 1px) 0 0 / 28px 28px',
                }}
              >
                <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
                  {draft.edges.map((edge, index) => {
                    const from = nodeById(edge.from);
                    const to = nodeById(edge.to);
                    if (!from || !to) return null;
                    const x1 = from.position.x + 200;
                    const y1 = from.position.y + 54;
                    const x2 = to.position.x;
                    const y2 = to.position.y + 54;
                    return (
                      <g key={`${edge.from}-${edge.to}-${index}`}>
                        <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="var(--accent)" strokeWidth={1.6} markerEnd="url(#canvas-arrow)" />
                      </g>
                    );
                  })}
                  <defs>
                    <marker id="canvas-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                      <path d="M0 0 L8 4 L0 8 z" fill="var(--accent)" />
                    </marker>
                  </defs>
                </svg>

                {draft.nodes.map((node) => {
                  const state = run?.node_states?.[node.id];
                  return (
                    <article
                      key={node.id}
                      onPointerDown={(event) => {
                        if ((event.target as HTMLElement).closest('button,input,textarea,select')) return;
                        startDrag(event, node);
                      }}
                      onClick={() => {
                        if (connecting) connect(node.id);
                        else setSelectedNode(node.id);
                      }}
                      style={{
                        position: 'absolute',
                        left: node.position.x,
                        top: node.position.y,
                        width: 200,
                        padding: 10,
                        borderRadius: 10,
                        background: 'var(--surface)',
                        border: `1px solid ${
                          connecting === node.id
                            ? 'var(--accent)'
                            : selectedNode === node.id
                              ? 'var(--text-muted)'
                              : 'var(--line)'
                        }`,
                        cursor: 'grab',
                        userSelect: 'none',
                      }}
                    >
                      <div className="row row--tight" style={{ justifyContent: 'space-between', gap: 6 }}>
                        <strong style={{ fontSize: 12 }}>{node.label || node.id}</strong>
                        <span className="dim" style={{ fontSize: 10 }}>
                          {node.kind}
                        </span>
                      </div>
                      <p className="dim" style={{ margin: '6px 0 0', fontSize: 11, minHeight: 28 }}>
                        {node.kind === 'note'
                          ? node.text || 'A note on the canvas.'
                          : (node.prompt || 'No prompt yet').slice(0, 90)}
                      </p>
                      {state ? (
                        <p style={{ margin: '6px 0 0', fontSize: 10, color: state.status === 'failed' ? 'var(--danger)' : 'var(--text-dim)' }}>
                          {state.status}
                          {state.error ? ` — ${state.error.slice(0, 60)}` : ''}
                        </p>
                      ) : null}
                      <div className="row row--tight" style={{ marginTop: 8, gap: 4 }}>
                        <button
                          type="button"
                          className="btn btn--sm btn--icon"
                          title="Connect this node to another"
                          onClick={(event) => {
                            event.stopPropagation();
                            setConnecting(node.id);
                          }}
                        >
                          <Icon name="send" size={11} />
                        </button>
                        <button
                          type="button"
                          className="btn btn--sm btn--icon"
                          title="Remove this node"
                          onClick={(event) => {
                            event.stopPropagation();
                            removeNode(node.id);
                          }}
                        >
                          <Icon name="trash" size={11} />
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>

              {active ? (
                <div className="panel" style={{ padding: 'var(--s3)' }}>
                  <div className="row row--tight" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
                    <p className="panel__title" style={{ margin: 0 }}>
                      {active.kind === 'note' ? 'Note' : 'Generate node'}
                    </p>
                    <Button size="sm" onClick={() => setSelectedNode('')}>
                      Close
                    </Button>
                  </div>
                  <div className="stack stack--tight">
                    <TextInput
                      label="Label"
                      value={active.label}
                      onChange={(event) => patchNode(active.id, { label: event.target.value })}
                    />
                    {active.kind === 'note' ? (
                      <TextArea
                        label="Text"
                        rows={3}
                        value={active.text ?? ''}
                        onChange={(event) => patchNode(active.id, { text: event.target.value })}
                      />
                    ) : (
                      <>
                        <ModelPicker
                          models={eligibleModels.filter((model) => model.modality === 'image' || model.modality === 'video')}
                          value={active.model_id ?? null}
                          onChange={(id) => patchNode(active.id, { model_id: id })}
                          modality="all"
                        />
                        <TextArea
                          label="Prompt"
                          rows={3}
                          value={active.prompt ?? ''}
                          onChange={(event) => patchNode(active.id, { prompt: event.target.value })}
                        />
                        <TextInput
                          label="Negative prompt"
                          value={active.negative_prompt ?? ''}
                          onChange={(event) => patchNode(active.id, { negative_prompt: event.target.value })}
                        />
                        <p className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
                          Mode is chosen at run time from what the node has: text-to-X when nothing feeds it,
                          image-to-X when an input edge carries a stored output.
                        </p>
                      </>
                    )}
                  </div>
                </div>
              ) : null}

              {draft.edges.length ? (
                <div className="panel" style={{ padding: 'var(--s3)' }}>
                  <p className="panel__title" style={{ marginBottom: 8 }}>
                    Edges
                  </p>
                  <div className="stack stack--tight">
                    {draft.edges.map((edge, index) => (
                      <div key={`${edge.from}-${edge.to}-${index}`} className="row row--tight" style={{ justifyContent: 'space-between' }}>
                        <span style={{ fontSize: 12 }}>
                          {nodeById(edge.from)?.label || edge.from} → {nodeById(edge.to)?.label || edge.to}{' '}
                          <span className="dim">({edge.role})</span>
                        </span>
                        <Button size="sm" onClick={() => removeEdge(index)}>
                          Remove
                        </Button>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {run ? (
                <div className="panel" style={{ padding: 'var(--s3)' }}>
                  <p className="panel__title" style={{ marginBottom: 8 }}>
                    Run {run.status}
                  </p>
                  <div className="stack stack--tight">
                    {(run.node_order ?? draft.nodes.map((node) => node.id)).map((nodeId) => {
                      const state = run.node_states[nodeId];
                      const node = nodeById(nodeId);
                      return (
                        <div key={nodeId} className="row row--tight" style={{ justifyContent: 'space-between', gap: 8 }}>
                          <span style={{ fontSize: 12 }}>{node?.label || nodeId}</span>
                          <span className="dim" style={{ fontSize: 11 }}>
                            {state?.status ?? 'not reached'}
                            {state?.job_id ? ` · job ${state.job_id.slice(0, 8)}` : ''}
                            {state?.error ? ` — ${state.error}` : ''}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : null}
            </>
          )}
        </section>
      </div>

      <NewCanvas
        open={creating}
        onClose={() => setCreating(false)}
        onCreate={async (name) => {
          setCreating(false);
          try {
            const created = await canvasesApi.create({ name, nodes: [], edges: [] });
            invalidate('canvases');
            list.reload();
            setSelectedId(created.id);
            setDraft(created);
            toasts.push({ tone: 'success', title: 'Canvas created', text: 'Add a generate node to run it.' });
          } catch (error) {
            toasts.push({
              tone: 'error',
              title: 'The canvas was refused',
              text: error instanceof Error ? error.message : 'Could not create the canvas.',
            });
          }
        }}
      />
    </div>
  );
}

function NewCanvas({
  open,
  onClose,
  onCreate,
}: {
  open: boolean;
  onClose: () => void;
  onCreate: (name: string) => void | Promise<void>;
}) {
  const [name, setName] = useState('Untitled canvas');
  useEffect(() => {
    if (open) setName('Untitled canvas');
  }, [open]);
  return (
    <Dialog open={open} onClose={onClose} title="New canvas" width="narrow">
      <TextInput label="Name" value={name} onChange={(event) => setName(event.target.value)} autoFocus />
      <div className="row row--tight" style={{ marginTop: 'var(--s4)', justifyContent: 'flex-end' }}>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={() => void onCreate(name.trim() || 'Untitled canvas')}>
          Create
        </Button>
      </div>
    </Dialog>
  );
}
