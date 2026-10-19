// The bundler handles these; TypeScript needs a declaration so that a
// side-effect import of a stylesheet is not an unchecked module reference.
declare module '*.css' {
  const content: string;
  export default content;
}

/**
 * The build-time environment Vite injects.
 *
 * `types: []` in tsconfig means no ambient `vite/client` types are pulled in, so
 * `import.meta.env` is declared here instead. Only variables the build actually
 * reads are listed: an entry here is a promise that something reads it, and a
 * variable nobody reads is a configuration knob that silently does nothing.
 */
interface ImportMetaEnv {
  /** Prefix in front of every Studio API route. Empty means same-origin. */
  readonly VITE_STUDIO_API_PREFIX?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
