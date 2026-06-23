export type DraftSource = 'deepseek' | 'rules_only' | string

export type AiSettingsSlice = {
  feature_ai_enabled: boolean
  feature_ai_extractor_enabled?: boolean
  feature_semantic_enabled?: boolean
}

export type CandidateAiMeta = {
  draft_source?: string | null
  draft_model?: string | null
  draft_ai_error?: string | null
}

