import { useEffect, useMemo, useRef, useState } from 'react';
import { chat } from '../../api/endpoints.ts';
import type { ChatReply } from '../../api/types.ts';
import { Badge, MockBadge } from '../../components/Badge.tsx';
import { Button } from '../../components/Button.tsx';
import { Select, TextArea } from '../../components/Field.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { Icon } from '../../components/Icon.tsx';
import { EmptyState, ErrorState, LoadingState } from '../../components/State.tsx';
import { Link } from '../../app/router.tsx';
import { useQuery } from '../../state/query.ts';

interface Exchange {
  role: 'user' | 'assistant';
  content: string;
  model?: string;
  latency_ms?: number;
  usage?: Record<string, unknown>;
}

/**
 * Conversation on the workspace's own key.
 *
 * Nothing here is simulated: every assistant line is the provider's answer to
 * the messages above it, and a provider failure is shown as a failure. The model
 * picker lists only models registered as conversation models, so an image model
 * cannot be selected and then fail on every send.
 */
export function ChatScreen() {
  const models = useQuery('chat-models', () => chat.models(), { staleMs: 10_000 });
  const [modelId, setModelId] = useState('');
  const [draft, setDraft] = useState('');
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const logRef = useRef<HTMLDivElement | null>(null);

  const list = models.data?.models ?? [];
  const selected = list.find((item) => item.id === modelId) ?? list[0];

  useEffect(() => {
    if (modelId && list.some((item) => item.id === modelId)) return;
    const preferred = models.data?.defaultModelId;
    if (preferred && list.some((item) => item.id === preferred)) {
      setModelId(preferred);
    } else if (list.length) {
      setModelId(list[0].id);
    }
  }, [models.data, list, modelId]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [exchanges, sending]);

  const options = useMemo(
    () => list.map((model) => ({ value: model.id, label: `${model.display_name} — ${model.provider.connection_name}` })),
    [list],
  );

  async function send(textOverride?: string) {
    const text = (textOverride ?? draft).trim();
    if (!selected) return;
    if (!text && !exchanges.length) return;
    const next = text ? [...exchanges, { role: 'user' as const, content: text }] : exchanges;
    if (text) {
      setExchanges(next);
      setDraft('');
    }
    setError(null);
    setSending(true);
    try {
      const reply: ChatReply = await chat.send({
        model_id: selected.id,
        messages: next.map((item) => ({ role: item.role, content: item.content })),
        max_tokens: 1024,
      });
      setExchanges((current) => [
        ...(text ? current : next),
        {
          role: 'assistant',
          content: reply.message.content,
          model: reply.model_key,
          latency_ms: reply.latency_ms,
          usage: reply.usage,
        },
      ]);
    } catch (problem) {
      setError(problem);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__heading">Chat</h1>
          <p className="page__subtitle">
            A conversation with a model you registered. The request goes from your browser to this backend to your
            provider, on your key. The provider bills the turn; this platform adds nothing.
          </p>
        </div>
        <div className="page__actions">
          <Link to="/onboard" className="btn btn--sm">
            <span className="btn__icon">
              <Icon name="plus" size={14} />
            </span>
            Add a provider
          </Link>
          {exchanges.length ? (
            <Button
              size="sm"
              onClick={() => {
                setExchanges([]);
                setError(null);
              }}
            >
              Clear
            </Button>
          ) : null}
        </div>
      </div>

      {models.loading ? <LoadingState label="Reading your conversation models…" /> : null}
      {models.error ? <ErrorState error={models.error} onRetry={models.reload} /> : null}

      {!models.loading && !models.error && !list.length ? (
        <EmptyState
          icon="send"
          title="No conversation model yet"
          body="A chat model is registered as 'other' by the one-key onboarding screen. Add a provider and tick its chat models; this screen will list them."
          action={
            <Link to="/onboard" className="btn btn--primary">
              Add a provider from one key
            </Link>
          }
        />
      ) : null}

      {list.length ? (
        <>
          <div className="panel">
            <div className="row row--tight" style={{ alignItems: 'flex-end' }}>
              <div style={{ flex: 1, minWidth: 220 }}>
                <Select
                  label="Model"
                  value={selected?.id ?? ''}
                  onChange={(event) => setModelId(event.target.value)}
                  options={options}
                  hint="Only models registered as conversation models appear here."
                />
              </div>
              <div className="row row--tight" style={{ paddingBottom: 'var(--s4)' }}>
                {selected ? (
                  <>
                    <Badge tone="plain">{selected.provider.adapter_label}</Badge>
                    <MockBadge isMock={selected.provider.is_mock} />
                  </>
                ) : null}
              </div>
            </div>
            {models.data?.billing ? (
              <p className="muted" style={{ margin: 'var(--s2) 0 0' }}>
                {models.data.billing}
              </p>
            ) : null}
          </div>

          <div
            ref={logRef}
            className="panel"
            style={{ maxHeight: '52vh', overflowY: 'auto', background: 'var(--bg-inset)' }}
            aria-live="polite"
          >
            {!exchanges.length ? (
              <p className="muted" style={{ margin: 0 }}>
                No turns yet. Say something; the answer is the provider's, not a placeholder.
              </p>
            ) : (
              <div className="stack">
                {exchanges.map((item, index) => (
                  <div
                    key={`${item.role}-${index}`}
                    className="card"
                    style={{
                      background: item.role === 'user' ? 'var(--bg-raised)' : 'var(--bg-sunken)',
                      borderColor: item.role === 'assistant' ? 'var(--line-strong)' : 'var(--line)',
                    }}
                  >
                    <div className="card__head" style={{ marginBottom: 'var(--s1)' }}>
                      <span className="card__title">{item.role === 'user' ? 'You' : 'Assistant'}</span>
                      {item.model ? <span className="mono dim">{item.model}</span> : null}
                      {item.latency_ms !== undefined ? (
                        <span className="dim">{item.latency_ms} ms</span>
                      ) : null}
                      {item.usage && usedTokens(item.usage) ? (
                        <span className="dim">{usedTokens(item.usage)} tokens</span>
                      ) : null}
                    </div>
                    <p style={{ margin: 0, whiteSpace: 'pre-wrap', lineHeight: 1.55 }}>{item.content}</p>
                  </div>
                ))}
                {sending ? (
                  <p className="muted" style={{ margin: 0 }}>
                    Waiting for {selected?.display_name ?? 'the provider'}…
                  </p>
                ) : null}
              </div>
            )}
          </div>

          {error ? (
            <ErrorNotice
              error={error}
              onRetry={() => send()}
              retryLabel="Send the same conversation again"
            />
          ) : null}

          <div className="panel">
            <TextArea
              label="Message"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="Ask for a shot list, a prompt rewrite, or anything the model can answer…"
              rows={3}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                  event.preventDefault();
                  void send();
                }
              }}
            />
            <div className="row row--tight" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
              <span className="dim">
                Ctrl/⌘ + Enter sends. {selected ? `Answering with ${selected.display_name}.` : ''}
              </span>
              <Button
                variant="primary"
                onClick={() => send()}
                disabled={sending || !draft.trim()}
                disabledReason="Type a message first."
              >
                <span className="btn__icon">
                  <Icon name="send" size={14} />
                </span>
                {sending ? 'Sending…' : 'Send'}
              </Button>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

function usedTokens(usage: Record<string, unknown>): number {
  const prompt = typeof usage.prompt_tokens === 'number' ? usage.prompt_tokens : 0;
  const completion = typeof usage.completion_tokens === 'number' ? usage.completion_tokens : 0;
  const total = typeof usage.total_tokens === 'number' ? usage.total_tokens : prompt + completion;
  return Number.isFinite(total) ? total : 0;
}
