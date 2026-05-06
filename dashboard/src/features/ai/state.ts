import type { AiSettingsSlice } from './types'

export const withAiToggle = <T extends AiSettingsSlice>(settings: T, enabled: boolean): T => ({
  ...settings,
  feature_ai_enabled: enabled,
})

