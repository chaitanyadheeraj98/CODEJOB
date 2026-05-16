export function withSavedQueries<T extends { saved_gmail_queries: string[] }>(
  settingsPayload: T,
  savedQueries: string[],
): T {
  return { ...settingsPayload, saved_gmail_queries: savedQueries }
}
