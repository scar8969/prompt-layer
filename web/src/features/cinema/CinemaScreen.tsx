import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { cinema as cinemaApi, presets as presetsApi } from '../../api/endpoints.ts';
import type { CinemaBrief, CinemaGroup, CinemaOption, CompiledBrief } from '../../api/types.ts';
import { useRouter } from '../../app/router.tsx';
import { Icon } from '../../components/Icon.tsx';
import { useQuery } from '../../state/query.ts';
import { useToasts } from '../../state/toasts.tsx';

/**
 * The Director's Panel.
 *
 * Every control here adds a clause to one prompt, and the panel shows you the
 * clause before you commit to it. The right-hand column is the point of the
 * screen: the compiled prompt with each span attributed to the control that
 * produced it, and a ledger that can switch any clause off.
 *
 * Nothing on this screen invents a value. A control left alone contributes
 * nothing, and the ledger says which clauses are missing rather than filling
 * them with a default the model would have chosen anyway.
 */

type Modality = 'image' | 'video';

const GROUP_ORDER = ['subject', 'framing', 'camera', 'exposure', 'light', 'colour', 'mood', 'motion', 'finish', 'frame'];

export function CinemaScreen() {
  const { route, navigate } = useRouter();
  const toasts = useToasts();
  const vocabulary = useQuery('cinema:vocabulary', () => cinemaApi.vocabulary(), { staleMs: 300_000 });

  const [brief, setBrief] = useState<CinemaBrief>({
    subject: 'a woman in a wet overcoat, collar up',
    setting: 'a neon-lit street at two in the morning, rain on the pavement',
    shots: 'medium_close',
    angles: 'eye',
    compositions: 'thirds',
    bodies: 'arri_alexa_35',
    lenses: 'panavision_c',
    focal_lengths: '50',
    apertures: '1.8',
    shutters: '180',
    lighting: 'neon_practicals',
    palettes: 'teal_orange',
    grain: 'fine',
    moods: 'longing',
    movement: 'dolly_in',
    aspects: '2.39:1',
  });
  const [modality, setModality] = useState<Modality>('video');
  const [disabled, setDisabled] = useState<string[]>([]);
  const [compiled, setCompiled] = useState<CompiledBrief | null>(null);
  const [compiling, setCompiling] = useState(false);
  const [compileError, setCompileError] = useState<unknown>(null);
  const [openGroups, setOpenGroups] = useState<string[]>(['subject', 'framing', 'light']);

  // One compile per settled brief. The timer is the debounce; the sequence
  // number is what stops a slow answer overwriting a newer one.
  const timer = useRef<number | null>(null);
  const seq = useRef(0);

  const compile = useCallback(
    async (nextBrief: CinemaBrief, nextModality: Modality, nextDisabled: string[]) => {
      const ticket = ++seq.current;
      setCompiling(true);
      try {
        const result = await cinemaApi.compile({ brief: nextBrief, modality: nextModality, disabled: nextDisabled });
        if (ticket !== seq.current) return;
        setCompiled(result);
        setCompileError(null);
      } catch (error) {
        if (ticket !== seq.current) return;
        setCompileError(error);
      } finally {
        if (ticket === seq.current) setCompiling(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (!vocabulary.data) return;
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      void compile(brief, modality, disabled);
    }, 180);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [brief, modality, disabled, compile, vocabulary.data]);

  // Arriving from a look (`/cinema?look=<id>`) seeds the subject with that
  // preset's own template — the panel's words stay the panel's words.
  const lookId = route.params.get('look');
  useEffect(() => {
    if (!lookId) return;
    void (async () => {
      try {
        const preset = await presetsApi.get(lookId);
        if (preset.prompt_template) setBrief((prev) => ({ ...prev, subject: preset.prompt_template ?? '' }));
        if (preset.aspect_ratio) {
          setBrief((prev) => ({ ...prev, aspects: aspectKey(preset.aspect_ratio ?? '') }));
        }
        toasts.push({ tone: 'info', title: 'Look loaded', text: `${preset.name} is now the subject.` });
      } catch {
        toasts.push({ tone: 'error', title: 'That look could not be read' });
      }
    })();
    // The parameter is consumed once; a reload should not re-seed over edits.
    navigate('/cinema', { replace: true });
  }, [lookId, navigate, toasts]);

  const groups = useMemo<CinemaGroup[]>(() => {
    const all = vocabulary.data?.groups ?? [];
    return [...all].sort((a, b) => GROUP_ORDER.indexOf(a.key) - GROUP_ORDER.indexOf(b.key));
  }, [vocabulary.data]);

  const set = (name: string, value: string) => {
    setBrief((prev) => {
      const next = { ...prev };
      if (!value || next[name] === value) delete next[name];
      else next[name] = value;
      return next;
    });
  };

  const toggleSegment = (key: string) => {
    setDisabled((prev) => (prev.includes(key) ? prev.filter((item) => item !== key) : [...prev, key]));
  };

  const randomise = () => {
    if (!vocabulary.data) return;
    const pick = (options: CinemaOption[]) => options[Math.floor(Math.random() * options.length)]?.key ?? '';
    const byName = (name: string) =>
      groups.flatMap((group) => group.controls).find((control) => control.name === name)?.options ?? [];
    setBrief((prev) => ({
      ...prev,
      shots: pick(byName('shots')),
      angles: pick(byName('angles')),
      compositions: pick(byName('compositions')),
      bodies: pick(byName('bodies')),
      lenses: pick(byName('lenses')),
      focal_lengths: pick(byName('focal_lengths')),
      apertures: pick(byName('apertures')),
      lighting: pick(byName('lighting')),
      palettes: pick(byName('palettes')),
      moods: pick(byName('moods')),
      grain: pick(byName('grain')),
      movement: pick(byName('movement')),
    }));
    toasts.push({ tone: 'info', title: 'Rolled a new look', text: 'Every clause changed. The prompt is below.' });
  };

  const compiledPrompt = compiled?.prompt ?? '';
  const negative = compiled?.negative_prompt ?? '';

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(compiledPrompt);
      toasts.push({ tone: 'success', title: 'Prompt copied', text: `${compiledPrompt.length} characters.` });
    } catch {
      toasts.push({ tone: 'error', title: 'The clipboard refused' });
    }
  };

  const sendToStudio = () => {
    const base = modality === 'video' ? '/video' : '/image';
    const params = new URLSearchParams();
    if (compiledPrompt) params.set('prompt', compiledPrompt);
    if (negative) params.set('negative', negative);
    if (compiled?.aspect_ratio) params.set('aspect', compiled.aspect_ratio);
    if (modality === 'video' && typeof brief.movement === 'string') params.set('move', brief.movement);
    navigate(`${base}?${params.toString()}`);
  };

  return (
    <div>
      <div className="hf-page-head">
        <div>
          <p className="hf-eyebrow">Director's panel · {vocabulary.data?.option_count ?? 0} craft options</p>
          <h1 className="hf-display">Build the shot, see the sentence.</h1>
          <p className="hf-lede">
            Body, glass, focal length, aperture, light, grade, performance. Each choice adds one clause, and the ledger
            on the right names the control that added it — so you can take it back before a model ever sees it.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <div className="hf-seg" role="group" aria-label="Output kind">
            {(['image', 'video'] as Modality[]).map((value) => (
              <button key={value} type="button" aria-pressed={modality === value} onClick={() => setModality(value)}>
                {value === 'image' ? 'Still' : 'Motion'}
              </button>
            ))}
          </div>
          <button type="button" className="hf-btn" onClick={randomise} title="Change every clause at once">
            <Icon name="dice" size={15} /> Roll
          </button>
        </div>
      </div>

      <div className="hf-cinema">
        <div className="hf-cinema__controls">
          {vocabulary.error ? (
            <div className="hf-warn">
              <Icon name="alert" size={14} />
              <span>The panel's vocabulary could not be read. {(vocabulary.error as Error)?.message ?? ''}</span>
            </div>
          ) : null}

          {groups.map((group, index) => {
            const isOpen = openGroups.includes(group.key);
            const summary = group.controls
              .map((control) => {
                const value = brief[control.name];
                if (typeof value !== 'string' || !value) return '';
                return control.options.find((option) => option.key === value)?.label ?? '';
              })
              .filter(Boolean)
              .join(' · ');
            return (
              <section className="hf-group" key={group.key}>
                <div
                  className="hf-group__head"
                  role="button"
                  tabIndex={0}
                  aria-expanded={isOpen}
                  onClick={() =>
                    setOpenGroups((prev) =>
                      prev.includes(group.key) ? prev.filter((item) => item !== group.key) : [...prev, group.key],
                    )
                  }
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      setOpenGroups((prev) =>
                        prev.includes(group.key) ? prev.filter((item) => item !== group.key) : [...prev, group.key],
                      );
                    }
                  }}
                >
                  <span className="hf-group__index">{String(index + 1).padStart(2, '0')}</span>
                  <span className="hf-group__title">{group.label}</span>
                  <span className="hf-group__summary">{summary || group.summary}</span>
                  <Icon name={isOpen ? 'chevronDown' : 'chevronRight'} size={14} />
                </div>

                {isOpen ? (
                  <div className="hf-group__body">
                    {group.controls.map((control) => (
                      <div key={control.name}>
                        <div className="hf-field__label">
                          <span>{control.label}</span>
                          {control.kind === 'choice' ? (
                            <em>{control.options.length} options</em>
                          ) : (
                            <em>free text</em>
                          )}
                        </div>

                        {control.kind === 'text' ? (
                          <textarea
                            className="hf-textarea"
                            value={typeof brief[control.name] === 'string' ? (brief[control.name] as string) : ''}
                            placeholder={
                              control.name === 'subject'
                                ? 'Who or what is in the frame. This is the only control the panel will not fill for you.'
                                : 'Where it happens — the room, the street, the landscape.'
                            }
                            onChange={(event) => set(control.name, event.target.value)}
                          />
                        ) : (
                          <div className="hf-options">
                            <button
                              type="button"
                              className="hf-option hf-option--none"
                              aria-pressed={!brief[control.name]}
                              onClick={() => set(control.name, '')}
                              title="Leave this clause out of the prompt"
                            >
                              None
                            </button>
                            {control.options.map((option) => (
                              <button
                                key={option.key}
                                type="button"
                                className="hf-option"
                                aria-pressed={brief[control.name] === option.key}
                                onClick={() => set(control.name, option.key)}
                                title={option.note ? `${option.adds}\n\n${option.note}` : option.adds}
                              >
                                {option.label}
                              </button>
                            ))}
                          </div>
                        )}

                        {control.name === 'lighting' && brief.lighting ? (
                          <p className="hf-hintline">
                            {control.options.find((option) => option.key === brief.lighting)?.adds}
                          </p>
                        ) : null}
                      </div>
                    ))}

                    {group.key === 'frame' ? (
                      <div>
                        <div className="hf-field__label">
                          <span>Negative prompt</span>
                          <em>sent with every request</em>
                        </div>
                        <textarea
                          className="hf-textarea"
                          value={typeof brief.negative === 'string' ? (brief.negative as string) : ''}
                          placeholder={vocabulary.data?.default_negative ?? ''}
                          onChange={(event) => set('negative', event.target.value)}
                        />
                        <p className="hf-hintline">
                          Empty means the studio's baseline negative, which is listed here as the placeholder.
                        </p>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>

        <aside className="hf-dock">
          <div className="hf-panel">
            <div className="hf-panel__head">
              <h2 className="hf-panel__title">The prompt, clause by clause</h2>
              <span className="hf-mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
                {compiling ? 'compiling…' : `${compiledPrompt.length} chars`}
              </span>
            </div>

            {compileError ? (
              <div className="hf-warn" style={{ marginBottom: 10 }}>
                <Icon name="alert" size={14} />
                <span>The panel could not compile this brief.</span>
              </div>
            ) : null}

            <p className="hf-compiled">
              {compiled?.segments
                .filter((segment) => segment.enabled)
                .map((segment, index) => (
                  <span
                    key={segment.key}
                    className={`seg seg--${segment.key}`}
                    data-off={false}
                    title={`${segment.label} — ${segment.source}`}
                    onClick={() => toggleSegment(segment.key)}
                  >
                    {index === 0 ? capitalise(segment.text) : `, ${segment.text}`}
                  </span>
                ))}
              {compiled?.segments.some((segment) => segment.enabled) ? '.' : 'Nothing selected yet.'}
            </p>

            <div className="row row--tight" style={{ marginTop: 16, gap: 8, flexWrap: 'wrap' }}>
              <button type="button" className="hf-btn hf-btn--sm hf-btn--primary" onClick={copy} disabled={!compiledPrompt}>
                <Icon name="copy" size={13} /> Copy
              </button>
              <button type="button" className="hf-btn hf-btn--sm" onClick={sendToStudio} disabled={!compiledPrompt}>
                <Icon name="sparkle" size={13} /> Send to {modality === 'video' ? 'video' : 'image'} studio
              </button>
              <button
                type="button"
                className="hf-btn hf-btn--sm hf-btn--ghost"
                onClick={() => {
                  setDisabled([]);
                  toasts.push({ tone: 'info', title: 'Every clause is back on' });
                }}
              >
                Reset switches
              </button>
            </div>
          </div>

          <div className="hf-panel">
            <div className="hf-panel__head">
              <h2 className="hf-panel__title">Injection ledger</h2>
              <span className="hf-mono" style={{ fontSize: 10, color: 'var(--text-dim)' }}>
                {compiled?.segments.filter((segment) => segment.enabled).length ?? 0}/
                {compiled?.segments.length ?? 0} on
              </span>
            </div>

            <div className="hf-ledger">
              {(compiled?.segments ?? []).map((segment) => (
                <label className="hf-ledger__row" key={segment.key} data-off={disabled.includes(segment.key)}>
                  <input
                    type="checkbox"
                    checked={!disabled.includes(segment.key)}
                    onChange={() => toggleSegment(segment.key)}
                  />
                  <span>
                    <span className="hf-ledger__label">{segment.label}</span>
                    <span className={`hf-ledger__text seg--${segment.key}`}>{segment.text}</span>
                  </span>
                  <span className="hf-ledger__src">{segment.source}</span>
                </label>
              ))}
              {!compiled?.segments.length ? (
                <p style={{ margin: 0, fontSize: 12, color: 'var(--text-dim)' }}>
                  Pick a control and its clause appears here with the words it contributes.
                </p>
              ) : null}
            </div>

            {compiled?.omitted.length ? (
              <p className="hf-hintline">
                Not in the prompt: {compiled.omitted.join(', ')}. An empty control contributes nothing — it is never
                filled with a guess.
              </p>
            ) : null}
          </div>

          {compiled?.warnings.length ? (
            <div className="hf-panel">
              <div className="hf-panel__head">
                <h2 className="hf-panel__title">Before you spend</h2>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {compiled.warnings.map((warning, index) => (
                  <div
                    className={warning.level === 'missing' ? 'hf-warn hf-warn--missing' : 'hf-warn'}
                    key={index}
                  >
                    <Icon name={warning.level === 'missing' ? 'alert' : 'info'} size={14} />
                    <span>{warning.text}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <div className="hf-panel">
            <div className="hf-panel__head">
              <h2 className="hf-panel__title">Resolved parameters</h2>
            </div>
            <dl className="hf-kv" style={{ borderTop: 'none', paddingTop: 0 }}>
              {Object.entries(compiled?.parameters ?? {}).map(([key, value]) => (
                <div key={key} style={{ display: 'contents' }}>
                  <dt>{key.replace(/_/g, ' ')}</dt>
                  <dd className="hf-mono">{String(value)}</dd>
                </div>
              ))}
              <dt>model</dt>
              <dd style={{ color: 'var(--text-dim)' }}>
                whichever you choose in the studio — the panel does not pick one for you
              </dd>
            </dl>
            <p className="hf-hintline">
              These are the parameters this panel can resolve on its own. Anything the model declares beyond them is
              set on the studio screen, where its schema is known.
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}

function capitalise(text: string): string {
  if (!text) return text;
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Aspect values are keys from the vocabulary; a preset stores the ratio text. */
function aspectKey(ratio: string): string {
  const known = ['2.39:1', '16:9', '4:3', '1:1', '4:5', '9:16', '3:2'];
  return known.includes(ratio) ? ratio : '';
}
