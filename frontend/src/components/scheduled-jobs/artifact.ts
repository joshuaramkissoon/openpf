/**
 * Resolve a stored scheduler artifact `output_path` to the relative path the
 * `/agent/artifacts/{path}` endpoint expects.
 *
 * Stored paths are absolute (e.g. `…/.claude/runtime/artifacts/scheduled/foo/20260602-070000.md`).
 * The artifacts endpoint serves from `.claude/runtime/artifacts/`, so we strip
 * everything up to and including the recognised root segment.
 */
export function resolveArtifactRelativePath(outputPath: string): string {
  const artIdx = outputPath.indexOf('artifacts/')
  if (artIdx >= 0) return outputPath.slice(artIdx + 'artifacts/'.length)

  const cronIdx = outputPath.indexOf('cron_logs/')
  if (cronIdx >= 0) return outputPath.slice(cronIdx + 'cron_logs/'.length)

  const runtimeIdx = outputPath.indexOf('.claude/runtime/')
  return runtimeIdx >= 0 ? outputPath.slice(runtimeIdx + '.claude/runtime/'.length) : outputPath
}
