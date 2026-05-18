export const QUERY_BUCKET_LIMIT = 10

export type QueryBucketResult =
  | { ok: true; next: string[] }
  | { ok: false; reason: 'empty' | 'duplicate' | 'limit'; next: string[] }
