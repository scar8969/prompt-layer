import { GenerationStudio } from '../studio/GenerationStudio.tsx';

/** Video Studio adds duration, aspect ratio, resolution, camera move and audio. */
export function VideoStudioScreen() {
  return <GenerationStudio kind="video" />;
}
