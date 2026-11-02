import { useEffect, useMemo, useState } from 'react';
import {
  assets as assetsApi,
  connections as connectionsApi,
  jobs as jobsApi,
  presets as presetsApi,
} from '../../api/endpoints.ts';
import type { Asset, Connection, Job, Preset } from '../../api/types.ts';
import { assetToMedia, MediaGrid } from '../../components/Media.tsx';
import { Link } from '../../app/router.tsx';
import { Icon } from '../../components/Icon.tsx';
import { useSession, useStatus, billingSentence } from '../../state/session.tsx';
import { formatRelative } from '../../lib/util.ts';
import { JobCard } from '../jobs/JobCard.tsx';

interface HomeData {
  jobs: Job[];
  assets: Asset[];
  connections: Connection[];
  looks: Preset[];
  films: Preset[];
}

/**
 * Home.
 *
 * A landing page, not a status panel: the first thing on it is the work you can
 * make, drawn from the catalogue that actually exists in this repository. The
 * backend report that used to sit above the fold is still here — it is the
 * honest footer of the page, where an operator looks and a customer does not.
 */
export function HomeScreen() {
  const session = useSession();
  const { status } = useStatus();
  const [data, setData] = useState<HomeData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const results = await Promise.allSettled([
        jobsApi.list({ limit: 4 }),
        assetsApi.list({ limit: 8 }),
        connectionsApi.list(),
        presetsApi.list({ modality: 'image' }),
        presetsApi.list({ modality: 'video' }),
      ]);
      if (cancelled) return;
      const [jobPage, assetPage, connectionList, lookList, filmList] = results;
      setData({
        jobs: jobPage.status === 'fulfilled' ? jobPage.value.jobs : [],
        assets: assetPage.status === 'fulfilled' ? assetPage.value.assets : [],
        connections: connectionList.status === 'fulfilled' ? connectionList.value : [],
        looks: lookList.status === 'fulfilled' ? lookList.value : [],
        films: filmList.status === 'fulfilled' ? filmList.value : [],
      });
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Four frames for the hero, one per category, so the first impression is a
  // range rather than four versions of the same portrait.
  const heroArt = useMemo(() => {
    const seen = new Set<string>();
    const picked: Preset[] = [];
    for (const preset of [...(data?.looks ?? []), ...(data?.films ?? [])]) {
      const category = preset.category ?? '';
      if (seen.has(category)) continue;
      seen.add(category);
      picked.push(preset);
      if (picked.length === 4) break;
    }
    return picked;
  }, [data]);

  // The marquee is ordered round-robin across categories: the same data, read
  // as a contact sheet instead of a run of one shelf.
  const marquee = useMemo(() => {
    const all = [...(data?.looks ?? []), ...(data?.films ?? [])];
    const byCategory = new Map<string, Preset[]>();
    for (const preset of all) {
      const key = preset.category ?? 'general';
      if (!byCategory.has(key)) byCategory.set(key, []);
      byCategory.get(key)!.push(preset);
    }
    const shelves = [...byCategory.values()];
    const ordered: Preset[] = [];
    for (let index = 0; ordered.length < 16 && shelves.some((shelf) => shelf.length); index += 1) {
      for (const shelf of shelves) {
        const item = shelf[index];
        if (item) ordered.push(item);
      }
    }
    return [...ordered, ...ordered];
  }, [data]);

  const totalLooks = (data?.looks.length ?? 0) + (data?.films.length ?? 0);

  const firstName = session.user?.display_name?.split(' ')[0] ?? '';
  const connected = (data?.connections.length ?? 0) > 0;

  return (
    <div>
      <section className="hf-hero">
        <div className="hf-hero__art" aria-hidden="true">
          {heroArt.map((preset) =>
            preset.thumbnail_url ? <img key={preset.id} src={preset.thumbnail_url} alt="" /> : null,
          )}
        </div>

        <div className="hf-hero__content">
          <p className="hf-eyebrow" style={{ letterSpacing: '0.22em' }}>
            Prompt Layer · bring your own key
          </p>
          <h1 className="hf-hero__title">
            Every model.
            <br />
            Your key. Nothing
            <br />
            rewritten in secret.
          </h1>
          <p className="hf-hero__sub">
            A cinematic studio over the endpoints you connect yourself. Choose a look, shape the shot clause by clause,
            and see what the provider will add to your prompt before you spend a cent.
          </p>

          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <Link to="/explore" className="hf-btn hf-btn--primary">
              <Icon name="compass" size={15} /> Explore {totalLooks || 70} looks
            </Link>
            <Link to="/cinema" className="hf-btn">
              <Icon name="clapper" size={15} /> Open the director's panel
            </Link>
            {!connected ? (
              <Link to="/connections" className="hf-btn hf-btn--ghost">
                <Icon name="key" size={15} /> Connect a provider
              </Link>
            ) : null}
          </div>
        </div>
      </section>

      {marquee.length ? (
        <div className="hf-marquee" aria-label="A sample of the catalogue">
          <div className="hf-marquee__track">
            {marquee.map((preset, index) =>
              preset.thumbnail_url ? (
                <Link
                  key={`${preset.id}-${index}`}
                  to={`/explore?look=${encodeURIComponent(preset.id)}`}
                  className="hf-marquee__item"
                  title={preset.name}
                >
                  <img src={preset.thumbnail_url} alt="" loading="lazy" />
                </Link>
              ) : null,
            )}
          </div>
        </div>
      ) : null}

      <div className="hf-tiles">
        <Link to="/cinema" className="hf-tile">
          <span className="hf-tile__art" aria-hidden="true">
            {heroArt[1]?.thumbnail_url ? <img src={heroArt[1].thumbnail_url} alt="" /> : null}
          </span>
          <span className="hf-tile__kicker">Direct</span>
          <span className="hf-tile__title">The panel</span>
          <span className="hf-tile__body">
            Body, glass, aperture, light and grade compiled into one sentence — with the ledger of who said what.
          </span>
        </Link>

        <Link to="/explore" className="hf-tile">
          <span className="hf-tile__art" aria-hidden="true">
            {heroArt[2]?.thumbnail_url ? <img src={heroArt[2].thumbnail_url} alt="" /> : null}
          </span>
          <span className="hf-tile__kicker">Look</span>
          <span className="hf-tile__title">Explore</span>
          <span className="hf-tile__body">
            Seventy sourced shot briefs — light, lens, grade, finish — ready to run on whichever model you connected.
          </span>
        </Link>

        <Link to="/prompt" className="hf-tile">
          <span className="hf-tile__art" aria-hidden="true">
            {heroArt[3]?.thumbnail_url ? <img src={heroArt[3].thumbnail_url} alt="" /> : null}
          </span>
          <span className="hf-tile__kicker">Control</span>
          <span className="hf-tile__title">Prompt Lab</span>
          <span className="hf-tile__body">
            What the endpoint adds: prompt optimisers, safety screens, canned negatives, pinned resolution. Sourced.
          </span>
        </Link>

        <Link to={connected ? '/image' : '/connections'} className="hf-tile">
          <span className="hf-tile__art" aria-hidden="true">
            {heroArt[0]?.thumbnail_url ? <img src={heroArt[0].thumbnail_url} alt="" /> : null}
          </span>
          <span className="hf-tile__kicker">{connected ? 'Run' : 'Setup'}</span>
          <span className="hf-tile__title">{connected ? 'Image Studio' : 'Connect a key'}</span>
          <span className="hf-tile__body">
            {connected
              ? 'Your connections are live. Submit a prompt and the job, its cost and its outputs land in History.'
              : 'One base URL, one key, one model id. The key is stored server-side and never shown again.'}
          </span>
        </Link>
      </div>

      {data && data.assets.length ? (
        <section>
          <div className="hf-section-head">
            <h2>Made in this workspace</h2>
            <Link to="/assets" className="hf-btn hf-btn--sm hf-btn--ghost">
              Library <Icon name="chevronRight" size={13} />
            </Link>
          </div>
          <MediaGrid items={data.assets.slice(0, 8).map(assetToMedia)} columns={4} />
        </section>
      ) : null}

      {data && data.jobs.length ? (
        <section>
          <div className="hf-section-head">
            <h2>Recent jobs</h2>
            <Link to="/history" className="hf-btn hf-btn--sm hf-btn--ghost">
              All history <Icon name="chevronRight" size={13} />
            </Link>
          </div>
          <div style={{ display: 'grid', gap: 8 }}>
            {data.jobs.map((job) => (
              <JobCard key={job.id} job={job} compact />
            ))}
          </div>
        </section>
      ) : null}

      {data && !connected ? (
        <section className="hf-empty" style={{ marginTop: 30 }}>
          <p className="hf-eyebrow" style={{ marginBottom: 10 }}>
            Nothing is connected yet
          </p>
          <p style={{ margin: '0 auto 18px', maxWidth: '52ch', lineHeight: 1.6 }}>
            The catalogue, the panel and the ledger all work without a key — they are prompt work, and prompt work is
            free. Generating needs one endpoint you control.
          </p>
          <Link to="/connections" className="hf-btn hf-btn--primary">
            Connect a provider
          </Link>
        </section>
      ) : null}

      <section style={{ marginTop: 44, borderTop: '1px solid var(--line-soft)', paddingTop: 20 }}>
        <div style={{ display: 'flex', gap: 26, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <div className="hf-stat">
            <span className="hf-stat__value">{totalLooks}</span>
            <span className="hf-stat__label">sourced looks</span>
          </div>
          <div className="hf-stat">
            <span className="hf-stat__value">{data?.connections.length ?? 0}</span>
            <span className="hf-stat__label">connections</span>
          </div>
          <div className="hf-stat">
            <span className="hf-stat__value">{status?.adapters.length ?? 0}</span>
            <span className="hf-stat__label">adapters</span>
          </div>
          <div style={{ flex: 1, minWidth: 260 }}>
            <p style={{ margin: 0, color: 'var(--text-dim)', fontSize: 11.5, lineHeight: 1.6 }}>
              {status ? billingSentence(status.billing) : 'Billing mode is reported by the backend.'}
              {status ? ` ${status.environment} · v${status.version}` : ''}
              {data?.jobs.length ? ` · last job ${formatRelative(data.jobs[0].created_at)}` : ''}
              {status?.mock_provider_enabled ? ' · the local placeholder provider is enabled' : ''}
              {loading ? ' · reading your workspace…' : ''}
            </p>
          </div>
          <Link to="/prompt" className="hf-btn hf-btn--sm hf-btn--ghost">
            How the layers work <Icon name="external" size={12} />
          </Link>
        </div>
      </section>

      {firstName ? <span className="visually-hidden">Signed in as {firstName}</span> : null}
    </div>
  );
}
