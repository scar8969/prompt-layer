// The bundler handles these; TypeScript needs a declaration so that a
// side-effect import of a stylesheet is not an unchecked module reference.
declare module '*.css' {
  const content: string;
  export default content;
}
