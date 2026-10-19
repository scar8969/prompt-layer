import { GenerationStudio } from '../studio/GenerationStudio.tsx';

/** Image Studio is the shared generation surface with image-only controls. */
export function ImageStudioScreen() {
  return <GenerationStudio kind="image" />;
}
