import { apiClient as api } from './client'
import type {
  DiffLine,
  DraftResult,
  Recipe,
  RecipeDetail,
  RecipeDraftFields,
  RecipeRun,
  RecipeSuggestion,
} from '../types/recipes'

const enc = encodeURIComponent

export async function listRecipes() {
  const { data } = await api.get<{ recipes: Recipe[]; suggestions: RecipeSuggestion[] }>('/recipes')
  return data
}

export async function getRecipe(slug: string) {
  const { data } = await api.get<RecipeDetail>(`/recipes/${enc(slug)}`)
  return data
}

/** Archie drafts a recipe from a sentence (nothing is saved). Can take ~20s. */
export async function draftRecipe(prompt: string) {
  const { data } = await api.post<DraftResult>('/recipes/draft', { prompt }, { timeout: 120000 })
  return data
}

export async function createRecipe(fields: Partial<RecipeDraftFields> & { note?: string; created_by?: 'user' | 'archie'; enabled?: boolean }) {
  const { data } = await api.post<Recipe>('/recipes', fields)
  return data
}

export async function patchRecipe(slug: string, patch: Partial<RecipeDraftFields> & { note?: string; author?: 'user' | 'archie' }) {
  const { data } = await api.patch<Recipe>(`/recipes/${enc(slug)}`, patch)
  return data
}

export async function setRecipeEnabled(slug: string, enabled: boolean) {
  const { data } = await api.post<Recipe>(`/recipes/${enc(slug)}/enabled`, { enabled })
  return data
}

/** Archie's proposed edit + diff (nothing is saved). */
export async function reviseRecipe(slug: string, instruction: string) {
  const { data } = await api.post<DraftResult>(`/recipes/${enc(slug)}/revise`, { instruction }, { timeout: 120000 })
  return data
}

export async function runRecipe(slug: string) {
  const { data } = await api.post<{ status: string; recipe: Recipe }>(`/recipes/${enc(slug)}/run`)
  return data
}

export async function listRecipeRuns(slug: string, limit = 20) {
  const { data } = await api.get<RecipeRun[]>(`/recipes/${enc(slug)}/runs`, { params: { limit } })
  return data
}

export async function getVersionDiff(slug: string, version: number, against?: number) {
  const { data } = await api.get<{ version: number; against: number | null; author: string; note: string; created_at: string; diff: DiffLine[] }>(
    `/recipes/${enc(slug)}/versions/${version}/diff`,
    { params: against ? { against } : {} },
  )
  return data
}

export async function revertRecipe(slug: string, version: number) {
  const { data } = await api.post<Recipe>(`/recipes/${enc(slug)}/revert`, { version })
  return data
}

export async function resetRecipe(slug: string) {
  const { data } = await api.post<Recipe>(`/recipes/${enc(slug)}/reset`)
  return data
}

export async function deleteRecipe(slug: string) {
  await api.delete(`/recipes/${enc(slug)}`)
}

export async function resolveSuggestion(id: string, decision: 'accept' | 'dismiss') {
  const { data } = await api.post<{ suggestion: RecipeSuggestion; recipe?: Recipe }>(`/recipes/suggestions/${enc(id)}/${decision}`)
  return data
}
