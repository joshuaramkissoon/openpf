import type { Route } from '@/lib/router'
import { RecipeDetailPage } from './detail'
import { RecipesGallery } from './gallery'
import { NewRecipe } from './new-recipe'

/**
 * Recipes — "Views, applied to workflows". Routes:
 *   /recipes            gallery (built-ins, made by Archie, suggestions, system jobs)
 *   /recipes/new        ask in a sentence → preview → save
 *   /recipes/:slug      detail: result, runs, instructions, edit with a sentence, history
 */
export function RecipesPage({ route }: { route: Route }) {
  const [, second] = route.segments
  if (!second) return <RecipesGallery />
  if (second === 'new') return <NewRecipe initialPrompt={route.query.get('prompt') ?? ''} autoStart={route.query.get('go') === '1'} />
  return <RecipeDetailPage key={second} slug={second} />
}
